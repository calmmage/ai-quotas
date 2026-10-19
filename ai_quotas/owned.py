"""Owned logins: a vendor login made *for the sampler*, refreshed only here.

Borrowed adapters read the logins of the CLIs you use (``~/.claude``,
``~/.codex``, ``~/.grok``). Claude and Codex are never refreshed: refresh
tokens rotate, so a refresh would log that CLI out. (The borrowed Grok adapter
still refreshes ``~/.grok/auth.json`` near expiry, as it did before owned
logins.) A login that no CLI uses then goes stale.

An owned login is a separate OAuth grant with its own refresh-token chain,
made once by ``ai-quotas login <provider> --name <name>``. Nothing else holds
that chain, so the sampler may refresh it and write the rotated tokens back.

Layout (``logins_dir`` = ``[logins] dir`` / ``AI_QUOTAS_LOGINS_DIR``)::

    <logins_dir>/<provider>-<name>/
        login.json          {"provider", "name", "account", "owned": true, "created_at"}
        .credentials.json   claude: {"claudeAiOauth": {...}}   (vendor format)
        auth.json           codex / grok                        (vendor format)
        state.json          last check / refresh, no secrets
        .lock               flock: one refresh at a time

Gate: refresh and write-back happen only for a dir directly inside
``logins_dir`` with ``owned: true`` in ``login.json``, whose name matches the
marker and whose credentials file is a regular file (not a symlink into a
CLI's home). Everything else raises :class:`NotOwned`.

Tokens never leave this module except to the vendor; errors carry status
codes and vendor error names only.
"""

from __future__ import annotations

import base64
import fcntl
import json
import os
import re
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from ai_quotas import config
from ai_quotas.accounts import normalize_account

MARKER = "login.json"
STATE = "state.json"
LOCK = ".lock"
CREDENTIALS = {"claude": ".credentials.json", "codex": "auth.json", "grok": "auth.json"}
PROVIDERS = tuple(CREDENTIALS)

# Refresh when the access token has less than this left.
REFRESH_MARGIN = timedelta(minutes=10)
# Codex access tokens live ~10 days; the CLI refreshes every 8. Keep the chain
# exercised even when the token itself is still valid.
CODEX_MAX_AGE = timedelta(days=7)
LOCK_TIMEOUT_S = 120.0

CLAUDE_TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
CLAUDE_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
CODEX_TOKEN_URL = "https://auth.openai.com/oauth/token"
CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"

_NAME = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,39}$")


class OwnedError(RuntimeError):
    """A problem with an owned login. Messages never contain tokens."""


class NotOwned(OwnedError):
    """The path is not an owned login; nothing may refresh or write it."""


class NeedsRelogin(OwnedError):
    """The refresh chain is gone (revoked, expired, missing). Only a new login fixes it."""

    def __init__(self, login: "Login", why: str):
        self.login = login
        self.why = why
        super().__init__(f"{why}; needs re-login: `{login.relogin_command}`")


@dataclass(frozen=True)
class Login:
    provider: str
    name: str
    path: Path
    account: str | None = None
    default: bool = False
    created_at: str | None = None

    @property
    def id(self) -> str:
        return f"{self.provider}-{self.name}"

    @property
    def creds_path(self) -> Path:
        return self.path / CREDENTIALS[self.provider]

    @property
    def relogin_command(self) -> str:
        move = config.logins_move_to()
        tail = f" --move-to {move}" if move else ""
        return f"ai-quotas login {self.provider} --name {self.name}{tail}"


def valid_name(name: str) -> bool:
    return bool(_NAME.match(name or ""))


def root_dir(root: Path | None = None) -> Path:
    return (root or config.logins_dir()).expanduser()


def login_path(provider: str, name: str, root: Path | None = None) -> Path:
    return root_dir(root) / f"{provider}-{name}"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise OwnedError(f"{path.name} is not a JSON object")
    return raw


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    """tmp in the same dir → fsync → rename → fsync dir. Mode 0600."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    blob = (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode()
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, blob)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    try:
        dfd = os.open(path.parent, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(dfd)
    except OSError:
        pass
    finally:
        os.close(dfd)


def write_marker(
    path: Path,
    *,
    provider: str,
    name: str,
    account: str | None,
    default: bool = False,
) -> None:
    marker: dict[str, Any] = {
        "provider": provider,
        "name": name,
        "account": normalize_account(account),
        "owned": True,
        "created_at": _iso(_now()),
    }
    if default:
        marker["default"] = True
    atomic_write_json(path / MARKER, marker)


def gate(path: Path, root: Path | None = None) -> Login:
    """The only way to get a :class:`Login`. Raises :class:`NotOwned`."""
    base = root_dir(root)
    if path.is_symlink():
        raise NotOwned(f"{path} is a symlink, not an owned login dir")
    try:
        real, real_root = path.resolve(strict=True), base.resolve(strict=True)
    except OSError as exc:
        raise NotOwned(f"{path}: {exc.strerror or exc}") from exc
    if real.parent != real_root:
        raise NotOwned(f"{path} is not directly inside the logins dir {base}")
    try:
        marker = read_json(real / MARKER)
    except (OSError, ValueError, OwnedError) as exc:
        raise NotOwned(f"{path} has no readable {MARKER}") from exc
    if marker.get("owned") is not True:
        raise NotOwned(f"{path}/{MARKER} does not say owned: true")
    provider, name = marker.get("provider"), marker.get("name")
    if provider not in CREDENTIALS or not isinstance(name, str) or not valid_name(name):
        raise NotOwned(f"{path}/{MARKER} names no known provider/name")
    if real.name != f"{provider}-{name}":
        raise NotOwned(f"{path} does not match its marker ({provider}-{name})")
    creds = real / CREDENTIALS[provider]
    if creds.is_symlink():
        raise NotOwned(f"{creds} is a symlink; an owned login keeps its own file")
    return Login(
        provider=provider,
        name=name,
        path=real,
        account=normalize_account(marker.get("account")),
        default=marker.get("default") is True,
        created_at=marker.get("created_at") if isinstance(marker.get("created_at"), str) else None,
    )


def discover(root: Path | None = None) -> tuple[list[Login], list[tuple[Path, str]]]:
    """(owned logins, [(dir, why it was skipped)]) under the logins dir."""
    base = root_dir(root)
    logins: list[Login] = []
    skipped: list[tuple[Path, str]] = []
    try:
        entries = sorted(p for p in base.iterdir() if p.is_dir() and not p.name.startswith("."))
    except OSError:
        return [], []
    for entry in entries:
        try:
            logins.append(gate(entry, base))
        except NotOwned as exc:
            skipped.append((entry, str(exc)))
    return logins, skipped


def find(ref: str, root: Path | None = None) -> Login:
    """``claude-work`` (dir name) or a path to the dir."""
    base = root_dir(root)
    candidate = base / ref if "/" not in ref else Path(ref).expanduser()
    if not candidate.is_dir():
        known = ", ".join(l.id for l in discover(base)[0]) or "none"
        raise NotOwned(f"no owned login {ref!r} in {base} (known: {known})")
    return gate(candidate, base)


@contextmanager
def locked(login: Login, timeout: float = LOCK_TIMEOUT_S) -> Iterator[None]:
    """Exclusive flock on the login dir; two samplers never refresh at once."""
    fd = os.open(login.path / LOCK, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise OwnedError(f"{login.id} is locked by another sampler for {timeout:.0f}s")
                time.sleep(0.2)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


# ---------------------------------------------------------------------------
# Per-vendor credential shapes
# ---------------------------------------------------------------------------


def _jwt_claims(token: Any) -> dict[str, Any]:
    """Unsigned claims of a JWT (exp, email). Never logged."""
    try:
        payload = str(token).split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return {}
    return claims if isinstance(claims, dict) else {}


def _from_epoch(value: Any) -> datetime | None:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    if seconds > 1e12:
        seconds /= 1000.0
    try:
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _from_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _grok_entry(creds: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    for key, entry in creds.items():
        if isinstance(entry, dict):
            return str(key), entry
    raise OwnedError("grok auth.json has no login entry")


def access_token(login: Login, creds: dict[str, Any]) -> str | None:
    if login.provider == "claude":
        oauth = creds.get("claudeAiOauth")
        token = oauth.get("accessToken") if isinstance(oauth, dict) else None
    elif login.provider == "codex":
        tokens = creds.get("tokens")
        token = tokens.get("access_token") if isinstance(tokens, dict) else None
    else:
        token = _grok_entry(creds)[1].get("key")
    return str(token) if token else None


def _refresh_token(login: Login, creds: dict[str, Any]) -> str | None:
    if login.provider == "claude":
        oauth = creds.get("claudeAiOauth")
        token = oauth.get("refreshToken") if isinstance(oauth, dict) else None
    elif login.provider == "codex":
        tokens = creds.get("tokens")
        token = tokens.get("refresh_token") if isinstance(tokens, dict) else None
    else:
        token = _grok_entry(creds)[1].get("refresh_token")
    return str(token) if token else None


def expires_at(login: Login, creds: dict[str, Any]) -> datetime | None:
    if login.provider == "claude":
        oauth = creds.get("claudeAiOauth")
        return _from_epoch(oauth.get("expiresAt")) if isinstance(oauth, dict) else None
    if login.provider == "codex":
        return _from_epoch(_jwt_claims(access_token(login, creds)).get("exp"))
    return _from_iso(_grok_entry(creds)[1].get("expires_at"))


def creds_account(login: Login, creds: dict[str, Any]) -> str | None:
    """Email stored with the credentials, when the vendor format has one."""
    if login.provider == "codex":
        tokens = creds.get("tokens")
        if isinstance(tokens, dict):
            return normalize_account(_jwt_claims(tokens.get("id_token")).get("email"))
        return None
    if login.provider == "grok":
        try:
            return normalize_account(_grok_entry(creds)[1].get("email"))
        except OwnedError:
            return None
    oauth = creds.get("claudeAiOauth")
    return normalize_account(oauth.get("email")) if isinstance(oauth, dict) else None


def needs_refresh(login: Login, creds: dict[str, Any], now: datetime | None = None) -> bool:
    now = now or _now()
    exp = expires_at(login, creds)
    if exp is not None and exp - now <= REFRESH_MARGIN:
        return True
    if login.provider == "codex":
        last = _from_iso(creds.get("last_refresh"))
        if last is None or now - last >= CODEX_MAX_AGE:
            return True
    return False


# ---------------------------------------------------------------------------
# Refresh
# ---------------------------------------------------------------------------

# A malformed request (invalid_request) is our bug, not a dead login: it stays
# an error so "needs re-login" never sends anyone through a useless login.
_RELOGIN_ERRORS = {"invalid_grant", "unauthorized_client", "invalid_client"}


def _error_name(body: bytes) -> str | None:
    try:
        data = json.loads(body.decode("utf-8", "replace"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    err = data.get("error")
    if isinstance(err, dict):
        err = err.get("type") or err.get("code") or err.get("message")
    return str(err)[:60] if err else None


def _post_token(login: Login, url: str, body: dict[str, Any], *, user_agent: str) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": user_agent,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        name = _error_name(exc.read(2000))
        if exc.code in (400, 401, 403) and (name in _RELOGIN_ERRORS or exc.code == 401):
            raise NeedsRelogin(login, f"refresh refused (HTTP {exc.code} {name or ''})".strip()) from None
        raise OwnedError(f"token refresh HTTP {exc.code}" + (f" {name}" if name else "")) from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise OwnedError(f"token refresh: {getattr(exc, 'reason', exc)}") from None
    except ValueError:
        raise OwnedError("token refresh answered non-JSON") from None
    if not isinstance(data, dict) or not data.get("access_token"):
        raise OwnedError("token refresh returned no access_token")
    return data


def _expiry_from(payload: dict[str, Any], now: datetime) -> datetime | None:
    try:
        seconds = int(payload.get("expires_in"))
    except (TypeError, ValueError):
        return None
    return now + timedelta(seconds=seconds) if seconds > 0 else None


def _refresh_claude(login: Login, creds: dict[str, Any]) -> dict[str, Any]:
    oauth = dict(creds.get("claudeAiOauth") or {})
    payload = _post_token(
        login,
        CLAUDE_TOKEN_URL,
        {
            "grant_type": "refresh_token",
            "refresh_token": _refresh_token(login, creds),
            "client_id": CLAUDE_CLIENT_ID,
        },
        user_agent="ai-quotas/claude",
    )
    now = _now()
    oauth["accessToken"] = payload["access_token"]
    if payload.get("refresh_token"):
        oauth["refreshToken"] = payload["refresh_token"]
    exp = _expiry_from(payload, now)
    if exp is not None:
        oauth["expiresAt"] = int(exp.timestamp() * 1000)
    if isinstance(payload.get("scope"), str) and payload["scope"].strip():
        oauth["scopes"] = payload["scope"].split()
    return {**creds, "claudeAiOauth": oauth}


def _refresh_codex(login: Login, creds: dict[str, Any]) -> dict[str, Any]:
    tokens = dict(creds.get("tokens") or {})
    payload = _post_token(
        login,
        CODEX_TOKEN_URL,
        {
            "client_id": CODEX_CLIENT_ID,
            "grant_type": "refresh_token",
            "refresh_token": _refresh_token(login, creds),
            "scope": "openid profile email",
        },
        user_agent="codex_cli_rs",
    )
    for key in ("access_token", "refresh_token", "id_token"):
        if payload.get(key):
            tokens[key] = payload[key]
    return {**creds, "tokens": tokens, "last_refresh": _now().isoformat().replace("+00:00", "Z")}


def _refresh_grok(login: Login, creds: dict[str, Any]) -> dict[str, Any]:
    from ai_quotas.adapters import grok

    key, entry = _grok_entry(creds)
    try:
        payload = grok._refresh_access_token(entry)
    except RuntimeError as exc:
        text = str(exc)
        if "HTTP 400" in text or "HTTP 401" in text or "invalid_grant" in text:
            raise NeedsRelogin(login, "refresh refused (" + text.split(":")[0] + ")") from None
        raise OwnedError(text.split(":")[0]) from None
    except (OSError, ValueError) as exc:
        raise OwnedError(f"token refresh: {type(exc).__name__}") from None
    return grok.refreshed_auth(creds, key, payload)


REFRESHERS: dict[str, Callable[[Login, dict[str, Any]], dict[str, Any]]] = {
    "claude": _refresh_claude,
    "codex": _refresh_codex,
    "grok": _refresh_grok,
}


def read_state(login: Login) -> dict[str, Any]:
    try:
        return read_json(login.path / STATE)
    except (OSError, ValueError, OwnedError):
        return {}


def update_state(login: Login, **fields: Any) -> None:
    """Non-secret bookkeeping for ``ai-quotas logins``. Never raises."""
    try:
        state = read_state(login)
        state.update(fields)
        atomic_write_json(login.path / STATE, state)
    except OSError:
        pass


def _load_creds(login: Login) -> dict[str, Any]:
    try:
        return read_json(login.creds_path)
    except FileNotFoundError:
        raise NeedsRelogin(login, f"no {login.creds_path.name} in {login.path}") from None
    except (OSError, ValueError, OwnedError) as exc:
        raise OwnedError(f"{login.creds_path.name} unreadable: {type(exc).__name__}") from None


def fresh_credentials(login: Login, *, force: bool = False) -> dict[str, Any]:
    """Credentials with a usable access token, refreshing under the lock.

    The returned dict holds tokens: pass it to the vendor call, never log it.
    ``force`` refreshes even when the token looks valid (after a 401).
    """
    # Re-check the gate: the dir may have changed since discovery.
    login = gate(login.path, login.path.parent)
    with locked(login):
        creds = _load_creds(login)
        if not access_token(login, creds) and not _refresh_token(login, creds):
            raise NeedsRelogin(login, "stored login has no tokens")
        if not (force or not access_token(login, creds) or needs_refresh(login, creds)):
            return creds
        if not _refresh_token(login, creds):
            raise NeedsRelogin(login, "access token expired and no refresh token is stored")
        try:
            new = REFRESHERS[login.provider](login, creds)
        except NeedsRelogin as exc:
            update_state(login, status="needs-relogin", reason=exc.why, checked_at=_iso(_now()))
            raise
        atomic_write_json(login.creds_path, new)
        exp = expires_at(login, new)
        update_state(
            login,
            refreshed_at=_iso(_now()),
            expires_at=_iso(exp) if exp else None,
        )
        return new


def status(login: Login, now: datetime | None = None) -> dict[str, Any]:
    """Offline summary for ``ai-quotas logins``. No network, no secrets."""
    now = now or _now()
    info: dict[str, Any] = {
        "login": login.id,
        "provider": login.provider,
        "account": login.account,
        "path": str(login.path),
        "state": "ok",
        "expires_at": None,
        "relogin": login.relogin_command,
    }
    state = read_state(login)
    for key in ("checked_at", "last_ok_at", "refreshed_at", "reason"):
        if state.get(key):
            info[key] = state[key]
    try:
        creds = read_json(login.creds_path)
    except FileNotFoundError:
        info.update(state="needs-relogin", reason=f"no {login.creds_path.name}")
        return info
    except (OSError, ValueError, OwnedError):
        info.update(state="error", reason=f"{login.creds_path.name} unreadable")
        return info
    try:
        exp = expires_at(login, creds)
        has_refresh = bool(_refresh_token(login, creds))
    except OwnedError as exc:
        info.update(state="error", reason=str(exc))
        return info
    info["expires_at"] = _iso(exp) if exp else None
    if state.get("status") == "needs-relogin":
        info["state"] = "needs-relogin"
    elif exp is not None and exp <= now and not has_refresh:
        info.update(state="needs-relogin", reason="expired, no refresh token")
    elif exp is not None and exp <= now:
        info["state"] = "refresh-due"
    elif state.get("status") and state.get("status") != "ok":
        info["state"] = str(state["status"])
    return info
