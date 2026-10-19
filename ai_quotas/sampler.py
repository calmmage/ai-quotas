"""``ai-quotas sampler``: one process per owned login (docs/SAMPLERS.md).

Each tick: lock → refresh if due → probe → write rows → Healthchecks ping.
Rows go to the hub database on this machine, or to the spool when this node
is a ``sampler`` (``[node] role``). A tick never raises: a dead vendor, a
revoked login or a full disk become rows / a ``/fail`` ping and the next tick
runs anyway. Nothing here sends alerts; only a hub does.
"""

from __future__ import annotations

import importlib
import re
import signal
import socket
import sys
import time
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ai_quotas import config, owned, spool
from ai_quotas.accounts import device_id, stamp as stamp_source
from ai_quotas.boosts import is_boost_row
from ai_quotas.reset_credits import is_reset_credit_row

SOURCE = "owned"
_TOKENISH = re.compile(r"[A-Za-z0-9_\-\.]{40,}")


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _scrub(text: Any) -> Any:
    """Belt and braces: no reason string may carry a token-shaped run."""
    if not isinstance(text, str):
        return text
    return _TOKENISH.sub("<redacted>", text)


def _fail_row(ts: str, login: owned.Login, status: str, reason: str) -> dict[str, Any]:
    return {
        "ts": ts,
        "provider": login.provider,
        "window": "unknown",
        "used_percent": None,
        "resets_at": None,
        "plan": None,
        "status": status,
        "reason": reason,
        "limit": None,
        "used": None,
    }


def snapshot(ts: str, login: owned.Login) -> list[dict[str, Any]]:
    """Adapter rows for one owned login. Never raises."""
    try:
        adapter = importlib.import_module(f"ai_quotas.adapters.{login.provider}")
        rows = adapter.snapshot_owned(ts, login)
    except owned.NeedsRelogin as exc:
        rows = [_fail_row(ts, login, "unavailable", str(exc))]
    except owned.NotOwned as exc:
        rows = [_fail_row(ts, login, "error", f"not an owned login: {exc}")]
    except owned.OwnedError as exc:
        rows = [_fail_row(ts, login, "error", str(exc))]
    except urllib.error.HTTPError as exc:
        rows = [_fail_row(ts, login, "error", f"HTTP {exc.code} from {login.provider}")]
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        # Vendor unreachable from this host: the login itself is fine.
        why = re.sub(r"^\[Errno -?\d+\]\s*", "", str(getattr(exc, "reason", None) or exc))
        rows = [_fail_row(ts, login, "error", f"network: {why}")]
    except Exception as exc:  # adapter bug: still a row, still a ping
        rows = [_fail_row(ts, login, "error", f"unexpected {type(exc).__name__}: {exc}")]
    return rows if isinstance(rows, list) else [_fail_row(ts, login, "error", "adapter returned non-list")]


def prepare(
    ts: str, login: owned.Login, rows: list[dict[str, Any]], *, device: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """(quota rows, reset-credit rows, boost rows), stamped as owned rows."""
    samples: list[dict[str, Any]] = []
    credits: list[dict[str, Any]] = []
    boosts: list[dict[str, Any]] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        row = dict(item)
        row["ts"] = ts
        row.setdefault("provider", login.provider)
        row["source"] = SOURCE
        row["login"] = login.id
        if not row.get("account") and login.account:
            row["account"] = login.account
        row.setdefault("account_default", bool(login.default))
        if "reason" in row:
            row["reason"] = _scrub(row["reason"])
        if is_reset_credit_row(row):
            credits.append(row)
        elif is_boost_row(row):
            boosts.append(row)
        else:
            if (row.get("status") or "error") != "ok":
                row["used_percent"] = None  # never a fake 0% on failure
            samples.append(row)
    stamp_source(samples, device=device)
    stamp_source(credits, device=device)
    return samples, credits, boosts


def output_mode(requested: str | None = None) -> str:
    """``db`` on a hub, ``spool`` on a sampler node, unless asked."""
    if requested in ("db", "spool"):
        return requested
    return "spool" if config.role() == "sampler" else "db"


def _write(
    mode: str,
    samples: list[dict[str, Any]],
    credits: list[dict[str, Any]],
    boosts: list[dict[str, Any]],
    *,
    login: owned.Login,
    device: str,
    database: Path,
) -> str:
    if mode == "spool":
        path = spool.append([*samples, *credits, *boosts], root=config.spool_dir(), device=device, login=login.id)
        return str(path)
    from ai_quotas.storage import append_reset_credits, append_samples, upsert_boosts

    append_samples(database, samples)
    append_reset_credits(database, credits)
    upsert_boosts(database, boosts)
    return str(database)


BORROWED_LOGIN = "borrowed"


def spool_borrowed(
    rows: list[dict[str, Any]],
    credits: list[dict[str, Any]],
    boosts: list[dict[str, Any]],
) -> dict[str, str]:
    """A sampler node's ``ai-quotas sample`` rows → spool, so a hub elsewhere
    still has this machine's local readings to fall back on."""
    out: dict[str, str] = {}
    try:
        path = spool.append([*rows, *credits, *boosts], root=config.spool_dir(),
                            device=device_id(), login=BORROWED_LOGIN)
        out["spool"] = str(path)
    except OSError as exc:
        out["spool"] = f"error:{type(exc).__name__}: {exc}"
    out["push"] = spool.push()
    return out


def hc_url(login: owned.Login) -> str:
    """Per-login check ``<prefix><login>``; '' when no ping key is set."""
    from ai_quotas.notify import DEFAULT_HC_BASE, ENV_HC_BASE, ENV_HC_PING_KEY, env_or_dotenv

    key = env_or_dotenv("AI_QUOTAS_HC_PING_KEY") or env_or_dotenv(ENV_HC_PING_KEY)
    if not key:
        return ""
    base = (env_or_dotenv(ENV_HC_BASE) or DEFAULT_HC_BASE).rstrip("/")
    return f"{base}/ping/{key}/{config.hc_slug_prefix()}{login.id}"


def ping(login: owned.Login, ok: bool) -> str:
    from ai_quotas.notify import ping_healthchecks

    url = hc_url(login)
    if not url:
        return "skip"
    # create=1: the check appears on first ping (self-hosted ping-key slugs).
    target = url + ("" if ok else "/fail") + "?create=1"
    result = ping_healthchecks(target)
    return "ok" if result == "ok" else "error"


def tick(
    login_ref: str,
    *,
    mode: str | None = None,
    database: Path | None = None,
) -> dict[str, Any]:
    """One sample of one owned login. Never raises."""
    ts = _now_iso()
    device = device_id()
    out: dict[str, Any] = {"ts": ts, "login": login_ref, "device": device}
    try:
        login = owned.find(login_ref)
    except owned.OwnedError as exc:
        out.update(status="error", reason=str(exc), ok=False)
        return out
    before = owned.read_state(login).get("refreshed_at")
    rows = snapshot(ts, login)
    samples, credits, boosts = prepare(ts, login, rows, device=device)
    ok_rows = [r for r in samples if r.get("status") == "ok"]
    first_bad = next((r for r in samples if r.get("status") != "ok"), None)
    status = "ok" if ok_rows else ((first_bad or {}).get("status") or "error")
    out.update(
        login=login.id,
        account=next((r.get("account") for r in samples if r.get("account")), login.account),
        status=status,
        ok=bool(ok_rows),
        windows={str(r.get("window")): r.get("used_percent") for r in ok_rows},
        rows=len(samples) + len(credits) + len(boosts),
    )
    if not ok_rows and first_bad is not None:
        out["reason"] = first_bad.get("reason")
    after = owned.read_state(login).get("refreshed_at")
    if after and after != before:
        out["refreshed"] = True
    mode = output_mode(mode)
    from ai_quotas.paths import database_path

    try:
        out["wrote"] = _write(mode, samples, credits, boosts, login=login, device=device,
                              database=database or database_path())
    except Exception as exc:
        out.update(ok=False, write_error=f"{type(exc).__name__}: {exc}")
    if mode == "spool":
        out["push"] = spool.push()
    state: dict[str, Any] = {
        "checked_at": ts,
        "status": "needs-relogin" if "needs re-login" in str(out.get("reason") or "") else status,
        "reason": out.get("reason"),
    }
    if ok_rows:
        state["last_ok_at"] = ts
    owned.update_state(login, **state)
    out["ping"] = ping(login, bool(out.get("ok")))
    return out


def format_tick(result: dict[str, Any]) -> str:
    parts = [result.get("ts", ""), str(result.get("login")), str(result.get("status"))]
    for window, pct in (result.get("windows") or {}).items():
        parts.append(f"{window} {pct:.0f}%" if isinstance(pct, (int, float)) else str(window))
    if result.get("refreshed"):
        parts.append("(refreshed)")
    if result.get("reason") and not result.get("ok"):
        parts.append(f"— {result['reason']}")
    if result.get("write_error"):
        parts.append(f"write failed: {result['write_error']}")
    if str(result.get("push", "")).startswith("error"):
        parts.append(f"push {result['push']}")
    return "  ".join(p for p in parts if p)


def run(
    login_ref: str,
    *,
    every: float | None = None,
    once: bool = False,
    mode: str | None = None,
    database: Path | None = None,
) -> int:
    """``--once``: one tick, exit 0 when ok. Else loop until SIGTERM/SIGINT."""
    if once:
        result = tick(login_ref, mode=mode, database=database)
        print(format_tick(result), flush=True)
        return 0 if result.get("ok") else 1
    interval = every or config.sampler_every()
    stop = {"now": False}

    def _stop(signum: int, _frame: Any) -> None:
        stop["now"] = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    print(f"sampler {login_ref} on {device_id()} ({socket.gethostname()}): every {interval:.0f}s, "
          f"output {output_mode(mode)}", flush=True)
    while not stop["now"]:
        started = time.monotonic()
        try:
            print(format_tick(tick(login_ref, mode=mode, database=database)), flush=True)
        except Exception as exc:  # tick() should not raise; never die if it does
            print(f"{_now_iso()}  {login_ref}  tick crashed: {type(exc).__name__}", file=sys.stderr, flush=True)
        deadline = started + interval
        while not stop["now"] and time.monotonic() < deadline:
            time.sleep(min(5.0, max(0.0, deadline - time.monotonic())))
    return 0
