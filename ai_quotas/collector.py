"""Sample all adapters and append contract rows to local storage.

Machine-oriented entry: ``python -m ai_quotas.collector``
(exit codes 0=OK, 1=WARN, 2=STOP).
"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import pkgutil
import sys
from pathlib import Path
from typing import Any, Callable

from ai_quotas import core
from ai_quotas.accounts import device_id, stamp as stamp_source
from ai_quotas.boosts import is_boost_row
from ai_quotas.reset_credits import is_reset_credit_row
from ai_quotas.paths import database_path, extra_adapters_dir, samples_path
from ai_quotas.storage import append_samples as append_stored_samples
from ai_quotas.storage import append_reset_credits as append_stored_reset_credits
from ai_quotas.storage import upsert_boosts as upsert_stored_boosts

# Built-in public adapters (agy excluded — private drop-in via AI_QUOTAS_EXTRA_ADAPTERS).
BUILTIN_ADAPTERS = ("claude", "codex", "grok", "openrouter", "orca", "muse")

SnapshotFn = Callable[[str], list[dict[str, Any]]]


def _load_module_from_path(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(f"ai_quotas_extra_{name}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load adapter {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def discover_adapters(
    *,
    extra_dir: Path | None = None,
) -> dict[str, SnapshotFn]:
    """Load built-in package adapters + optional drop-in private adapters.

    Extra adapters win on name collision (so a private override can replace a
    built-in). Each module must expose ``snapshot(ts) -> list[dict]``.
    """
    adapters: dict[str, SnapshotFn] = {}

    # Built-ins from ai_quotas.adapters
    try:
        import ai_quotas.adapters as adapters_pkg
    except ImportError:
        adapters_pkg = None

    if adapters_pkg is not None:
        pkg_path = getattr(adapters_pkg, "__path__", None)
        if pkg_path is not None:
            for info in pkgutil.iter_modules(pkg_path):
                if info.name.startswith("_"):
                    continue
                try:
                    mod = importlib.import_module(f"ai_quotas.adapters.{info.name}")
                except Exception:
                    continue
                fn = getattr(mod, "snapshot", None)
                if callable(fn):
                    adapters[info.name] = fn  # type: ignore[assignment]

    # Prefer the explicit built-in order; keep any others discovered
    ordered: dict[str, SnapshotFn] = {}
    for name in BUILTIN_ADAPTERS:
        if name in adapters:
            ordered[name] = adapters[name]
    for name, fn in adapters.items():
        if name not in ordered:
            ordered[name] = fn

    # Drop-in private adapters
    extra = extra_dir if extra_dir is not None else extra_adapters_dir()
    if extra is not None and extra.is_dir():
        for path in sorted(extra.glob("*.py")):
            if path.name.startswith("_"):
                continue
            name = path.stem
            try:
                mod = _load_module_from_path(name, path)
                fn = getattr(mod, "snapshot", None)
                if callable(fn):
                    ordered[name] = fn  # type: ignore[assignment]
            except Exception:
                # Discovery failure becomes a synthetic error row at sample time
                def _fail_factory(n: str = name, p: Path = path) -> SnapshotFn:
                    def _fail(ts: str) -> list[dict[str, Any]]:
                        return [
                            {
                                "ts": ts,
                                "provider": n,
                                "window": "unknown",
                                "used_percent": None,
                                "resets_at": None,
                                "plan": None,
                                "status": "error",
                                "reason": f"extra adapter load failed: {p.name}",
                                "limit": None,
                                "used": None,
                            }
                        ]

                    return _fail

                ordered[name] = _fail_factory()

    return ordered


def _error_row(ts: str, provider: str, reason: str) -> dict[str, Any]:
    return {
        "ts": ts,
        "provider": provider,
        "window": "unknown",
        "used_percent": None,
        "resets_at": None,
        "plan": None,
        "status": "error",
        "reason": reason,
        "limit": None,
        "used": None,
    }


def sample_all(
    ts: str | None = None,
    *,
    adapters: dict[str, SnapshotFn] | None = None,
) -> list[dict[str, Any]]:
    """Call snapshot(ts) on every adapter. Never crashes on a bad adapter.

    Returns quota rows only; reset-credit and boost rows are separate grains
    — use :func:`sample_all_split` to get all three.
    """
    return sample_all_split(ts, adapters=adapters)[0]


def sample_all_split(
    ts: str | None = None,
    *,
    adapters: dict[str, SnapshotFn] | None = None,
    providers: tuple[str, ...] | list[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """(quota_rows, reset_credit_rows, boost_rows) from every adapter.

    ``providers`` limits the probe to those adapter names. A name with no
    adapter becomes one error row and does not run the others.
    """
    ts = ts or core.now_iso()
    mods = dict(adapters if adapters is not None else discover_adapters())
    rows: list[dict[str, Any]] = []
    credits: list[dict[str, Any]] = []
    boosts: list[dict[str, Any]] = []
    if providers is not None:
        for name in providers:
            if name not in mods:
                rows.append(_error_row(ts, name, "no adapter"))
        mods = {name: mods[name] for name in providers if name in mods}
    for name, fn in mods.items():
        try:
            got = fn(ts)
            if not isinstance(got, list):
                rows.append(
                    _error_row(
                        ts,
                        name,
                        f"snapshot() returned non-list: {type(got).__name__}",
                    )
                )
                continue
            # Coerce shared tick timestamp; strip fabricated zeros on non-ok
            for item in got:
                if not isinstance(item, dict):
                    rows.append(
                        _error_row(ts, name, f"snapshot item is {type(item).__name__}")
                    )
                    continue
                row = dict(item)
                row["ts"] = ts
                if "provider" not in row or not row["provider"]:
                    row["provider"] = name
                if is_reset_credit_row(row):
                    credits.append(row)
                    continue
                if is_boost_row(row):
                    boosts.append(row)
                    continue
                status = row.get("status") or "error"
                if status != "ok":
                    # Contract: never leave a fake 0% on failure
                    if row.get("used_percent") is not None:
                        try:
                            if float(row["used_percent"]) == 0.0:
                                row["used_percent"] = None
                        except (TypeError, ValueError):
                            row["used_percent"] = None
                    if row.get("used_percent") is not None and status in {
                        "unavailable",
                        "error",
                    }:
                        # non-ok must not carry a used_percent
                        row["used_percent"] = None
                rows.append(row)
        except Exception as exc:
            rows.append(_error_row(ts, name, f"adapter load/call: {exc}"))
    # Source stamp: which machine sampled. Adapters set ``account`` (vendor
    # login email) when the vendor exposes it; it stays null otherwise.
    device = device_id()
    stamp_source(rows, device=device)
    stamp_source(credits, device=device)
    return rows, credits, boosts


def append_samples(
    rows: list[dict[str, Any]],
    path: str | Path | None = None,
) -> Path:
    """Append rows to SQLite (or an explicit legacy JSONL path)."""
    p = samples_path(path)
    append_stored_samples(p, rows)
    return p


def sample_now(
    *,
    path: str | Path | None = None,
    append: bool = True,
    ts: str | None = None,
    adapters: dict[str, SnapshotFn] | None = None,
    providers: tuple[str, ...] | list[str] | None = None,
) -> list[dict[str, Any]]:
    """Run adapters and optionally append to the samples file.

    ``providers`` probes only those names (see :func:`sample_all_split`).
    """
    rows, credits, boosts = sample_all_split(ts, adapters=adapters, providers=providers)
    if append:
        p = append_samples(rows, path)
        append_stored_reset_credits(p, credits)
        upsert_stored_boosts(p, boosts)
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Token-quota collector: sample providers, compute burn/need trends, "
            "emit STOP/WARN/OK (JSON). Human table: --pretty."
        )
    )
    parser.add_argument(
        "--no-sample",
        action="store_true",
        help="Skip live provider probes; evaluate from existing database only.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="JSON output (default when --pretty/--history is not set).",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Human table: used + burn/need rates + verdict.",
    )
    parser.add_argument(
        "--history",
        action="store_true",
        help="Peak used%% per reset period from stored samples (JSON unless --pretty).",
    )
    parser.add_argument(
        "--samples",
        type=str,
        default=None,
        help="Legacy JSONL path (fixtures/import compatibility).",
    )
    parser.add_argument(
        "--database",
        type=str,
        default=None,
        help="SQLite database path (default: ~/.local/share/ai-quotas/ai-quotas.sqlite3).",
    )
    args = parser.parse_args(argv)

    if args.database and args.samples:
        parser.error("--database and --samples are mutually exclusive")
    path = database_path(args.database) if args.database else samples_path(args.samples)
    ts = core.now_iso()

    if args.history:
        samples = core.load_samples(path)
        history = core.history_from_samples(samples)
        history["ts"] = ts
        if args.pretty:
            core.print_history(history)
        else:
            print(json.dumps(history, ensure_ascii=False))
        return 0

    if not args.no_sample:
        rows, credits, boosts = sample_all_split(ts)
        append_samples(rows, path)
        append_stored_reset_credits(path, credits)
        upsert_stored_boosts(path, boosts)

    samples = core.load_samples(path)
    from datetime import datetime, timezone

    now = core.parse_ts(ts) or datetime.now(timezone.utc).astimezone()
    result = core.evaluate(samples, now=now)
    result["ts"] = ts

    if args.pretty:
        core.print_pretty(result)
    else:
        print(json.dumps(result, ensure_ascii=False))
    return core.exit_code(result)


if __name__ == "__main__":
    sys.exit(main())
