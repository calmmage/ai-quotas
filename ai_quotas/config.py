"""Optional node config: ``~/.config/ai-quotas/config.toml`` (``AI_QUOTAS_CONFIG``).

Every key is optional and an environment variable always wins, so setups that
only use env vars keep working unchanged. Precedence: env > file > default.

    [node]
    role = "hub"              # hub (default) | sampler
    device = "my-laptop"      # else AI_QUOTAS_DEVICE / short hostname

    [logins]                  # owned logins (docs/SAMPLERS.md)
    dir = "~/.local/share/ai-quotas/logins"
    move_to = ""              # on a server: the --move-to its logins come from

    [sampler]
    every = "30m"             # default interval for `ai-quotas sampler`

    [spool]                   # where a sampler writes when it has no hub DB
    dir = "~/.local/share/ai-quotas/spool"
    push = ""                 # optional rsync destination after each tick

    [[ingest]]                # hub side, repeatable
    path = "~/.local/share/ai-quotas/inbox/cloud"
    pull = "user@host:/srv/ai-quotas/spool/"   # optional rsync source

    [accounts]                # primary-login pins (= AI_QUOTAS_ACCOUNT_<P>)
    claude = "me@example.com"

    [healthchecks]
    slug_prefix = "ai-quotas-"   # per-login check: ai-quotas-claude-work

Stdlib only (``tomllib``). A broken file is reported by ``make doctor`` and
otherwise treated as empty: config never stops a sample.
"""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ENV_CONFIG = "AI_QUOTAS_CONFIG"
ENV_ROLE = "AI_QUOTAS_ROLE"
ENV_LOGINS_DIR = "AI_QUOTAS_LOGINS_DIR"
ENV_LOGINS_MOVE_TO = "AI_QUOTAS_LOGINS_MOVE_TO"
ENV_SPOOL_DIR = "AI_QUOTAS_SPOOL_DIR"
ENV_SPOOL_PUSH = "AI_QUOTAS_SPOOL_PUSH"
ENV_INGEST = "AI_QUOTAS_INGEST"  # os.pathsep-separated local dirs (no pull)
ENV_SAMPLER_EVERY = "AI_QUOTAS_SAMPLER_EVERY"
ENV_HC_SLUG_PREFIX = "AI_QUOTAS_HC_SLUG_PREFIX"
ENV_SSH = "AI_QUOTAS_SSH"

DEFAULT_CONFIG = Path.home() / ".config" / "ai-quotas" / "config.toml"
ROLES = ("hub", "sampler")
DEFAULT_ROLE = "hub"
DEFAULT_SAMPLER_EVERY = 30 * 60
DEFAULT_HC_SLUG_PREFIX = "ai-quotas-"

_cache: tuple[str, int, dict[str, Any], str | None] | None = None


def config_path() -> Path:
    raw = (os.environ.get(ENV_CONFIG) or "").strip()
    return Path(raw).expanduser() if raw else DEFAULT_CONFIG


def _load() -> tuple[dict[str, Any], str | None]:
    """(parsed file, error). Cached by path + mtime."""
    global _cache
    path = config_path()
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        return {}, None
    if _cache and _cache[0] == str(path) and _cache[1] == mtime:
        return _cache[2], _cache[3]
    try:
        data: dict[str, Any] = tomllib.loads(path.read_text(encoding="utf-8"))
        error = None
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        data, error = {}, f"{path}: {exc}"
    _cache = (str(path), mtime, data, error)
    return data, error


def section(name: str) -> dict[str, Any]:
    value = _load()[0].get(name)
    return value if isinstance(value, dict) else {}


def _env(name: str) -> str | None:
    raw = os.environ.get(name)
    return raw.strip() if raw and raw.strip() else None


def _text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _data_dir() -> Path:
    from ai_quotas.paths import data_dir

    return data_dir()


def role() -> str:
    """``hub`` merges, renders and alerts; ``sampler`` only probes and writes."""
    value = (_env(ENV_ROLE) or _text(section("node").get("role")) or DEFAULT_ROLE).lower()
    return value if value in ROLES else DEFAULT_ROLE


def device() -> str | None:
    """Device name from the file; ``accounts.device_id`` adds env + hostname."""
    return _text(section("node").get("device"))


def logins_dir() -> Path:
    raw = _env(ENV_LOGINS_DIR) or _text(section("logins").get("dir"))
    return Path(raw).expanduser() if raw else _data_dir() / "logins"


def logins_move_to() -> str | None:
    """``HOST:DIR`` a re-login is moved to, for the fix command a server prints.

    A headless node can't open a browser, so its "needs re-login" message
    names the command to run on a laptop: ``ai-quotas login … --move-to``.
    """
    return _env(ENV_LOGINS_MOVE_TO) or _text(section("logins").get("move_to"))


def spool_dir() -> Path:
    raw = _env(ENV_SPOOL_DIR) or _text(section("spool").get("dir"))
    return Path(raw).expanduser() if raw else _data_dir() / "spool"


def spool_push() -> str | None:
    return _env(ENV_SPOOL_PUSH) or _text(section("spool").get("push"))


@dataclass(frozen=True)
class IngestSource:
    path: Path
    pull: str | None = None


def ingest_sources() -> list[IngestSource]:
    raw = _env(ENV_INGEST)
    if raw:
        return [IngestSource(Path(p).expanduser()) for p in raw.split(os.pathsep) if p.strip()]
    items = _load()[0].get("ingest")
    if isinstance(items, dict):
        items = [items]
    out: list[IngestSource] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        path = _text(item.get("path"))
        if path:
            out.append(IngestSource(Path(path).expanduser(), _text(item.get("pull"))))
    return out


def account_pin(provider: str) -> str | None:
    return _text(section("accounts").get(provider))


_DURATION = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([smhd]?)\s*$", re.IGNORECASE)


def parse_duration(value: Any) -> float | None:
    """``"30m"`` → 1800.0; bare numbers are seconds. None when unreadable."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value) if value > 0 else None
    if not isinstance(value, str):
        return None
    match = _DURATION.match(value)
    if not match:
        return None
    scale = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}[match.group(2).lower()]
    seconds = float(match.group(1)) * scale
    return seconds if seconds > 0 else None


def sampler_every() -> float:
    raw = _env(ENV_SAMPLER_EVERY) or section("sampler").get("every")
    return parse_duration(raw) or float(DEFAULT_SAMPLER_EVERY)


def hc_slug_prefix() -> str:
    raw = _env(ENV_HC_SLUG_PREFIX)
    if raw is None:
        raw = _text(section("healthchecks").get("slug_prefix"))
    return raw if raw is not None else DEFAULT_HC_SLUG_PREFIX


def ssh_binary() -> str:
    """``AI_QUOTAS_SSH``, else the system ``/usr/bin/ssh``, else ``ssh`` on PATH.

    The system binary first: under a macOS LaunchAgent PATH puts Homebrew's
    OpenSSH first, and it rejects Apple's ``UseKeychain`` in ~/.ssh/config.
    """
    override = _env(ENV_SSH)
    if override:
        return override
    return "/usr/bin/ssh" if os.access("/usr/bin/ssh", os.X_OK) else "ssh"


def doctor_lines() -> list[str]:
    path = config_path()
    _, error = _load()
    state = "error" if error else ("loaded" if path.is_file() else "absent (defaults)")
    lines = [f"config {path} — {state}"]
    if error:
        lines.append(f"  {error}")
    lines.append(f"  role     {role()}")
    lines.append(f"  logins   {logins_dir()}")
    lines.append(f"  spool    {spool_dir()}" + (f" → push {spool_push()}" if spool_push() else ""))
    sources = ingest_sources()
    if not sources:
        lines.append("  ingest   (none)")
    for src in sources:
        lines.append(f"  ingest   {src.path}" + (f" ← pull {src.pull}" if src.pull else ""))
    return lines
