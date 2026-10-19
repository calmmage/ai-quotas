"""Spool: how a sampler hands rows to a hub it cannot write to directly.

Append-only JSONL, one file per login per UTC day::

    <spool_dir>/<device>/<login>/YYYY-MM-DD.jsonl

Every line is one row of any grain (quota sample, ``kind: reset_credit``,
``kind: boost``). The spool dir is the whole transport contract: the hub
imports whatever lands in its inbox (``ai-quotas ingest``). How files travel
is up to you: same disk, a docker volume, the built-in rsync ``push`` /
``pull``, Syncthing. Import is idempotent, so re-running it is always safe.
"""

from __future__ import annotations

import fcntl
import json
import os
import shlex
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from ai_quotas import config
from ai_quotas.storage import import_spool_file, is_database

RSYNC_TIMEOUT_S = 120


def _safe(part: str) -> str:
    cleaned = "".join(c if c.isalnum() or c in "-_." else "_" for c in str(part)).strip("._")
    return cleaned or "unknown"


def file_for(root: Path, device: str, login: str, when: datetime | None = None) -> Path:
    day = (when or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m-%d")
    return root / _safe(device) / _safe(login) / f"{day}.jsonl"


def append(rows: Iterable[dict[str, Any]], *, root: Path, device: str, login: str) -> Path:
    """One write per tick under flock, so a reader never sees half a tick."""
    target = file_for(root, device, login)
    target.parent.mkdir(parents=True, exist_ok=True)
    blob = "".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n" for r in rows)
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        os.write(fd, blob.encode())
        os.fsync(fd)
    finally:
        os.close(fd)
    return target


def _remote(dest: str) -> tuple[str, str] | None:
    """``host:dir`` → (host, dir); None for a local path."""
    host, sep, path = dest.partition(":")
    if not sep or not host or "/" in host:
        return None
    return host, path


def _rsync(src: str, dest: str) -> str:
    """'ok' | 'skip' | 'error:…'. Never raises. Creates the destination dir."""
    binary = shutil.which("rsync")
    if not binary:
        return "error:rsync not installed"
    ssh = shlex.quote(config.ssh_binary())
    cmd = [binary, "-a", "--timeout=60", "-e", f"{ssh} -o BatchMode=yes -o ConnectTimeout=15"]
    remote = _remote(dest)
    if remote is None:
        try:
            Path(dest).expanduser().mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return f"error:{type(exc).__name__}: {exc}"
    elif remote[1]:
        cmd.append(f"--rsync-path=mkdir -p {shlex.quote(remote[1])} && rsync")
    cmd += [src, dest]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=RSYNC_TIMEOUT_S, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"error:{type(exc).__name__}"
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()
        return f"error:rsync exit {proc.returncode}" + (f": {tail[-1][:160]}" if tail else "")
    return "ok"


def push(root: Path | None = None, dest: str | None = None) -> str:
    """Sampler side: copy the spool to ``[spool] push``. 'skip' when unset."""
    dest = dest or config.spool_push()
    if not dest:
        return "skip"
    src = (root or config.spool_dir()).expanduser()
    if not src.is_dir():
        return "skip"
    return _rsync(str(src).rstrip("/") + "/", dest)


def pull(source: config.IngestSource) -> str:
    if not source.pull:
        return "skip"
    return _rsync(source.pull.rstrip("/") + "/", str(source.path.expanduser()).rstrip("/") + "/")


def ingest(
    database: Path,
    sources: list[config.IngestSource] | None = None,
    *,
    do_pull: bool = True,
) -> dict[str, Any]:
    """Pull (optional) and import every spool file under each source path."""
    sources = config.ingest_sources() if sources is None else sources
    report: dict[str, Any] = {"sources": [], "imported": 0, "files": 0, "errors": []}
    if not is_database(database):
        report["errors"].append(f"ingest needs a SQLite database, not {database}")
        return report
    for source in sources:
        entry: dict[str, Any] = {"path": str(source.path), "pull": "skip", "imported": 0, "files": 0}
        if do_pull:
            entry["pull"] = pull(source)
            if entry["pull"].startswith("error"):
                report["errors"].append(f"pull {source.pull}: {entry['pull'][6:]}")
        root = source.path.expanduser()
        for path in sorted(root.glob("*/*/*.jsonl")) if root.is_dir() else []:
            key = path.relative_to(root).as_posix()
            try:
                got = import_spool_file(database, path, key=key)
            except Exception as exc:  # one bad file never blocks the rest
                report["errors"].append(f"{key}: {type(exc).__name__}: {exc}")
                continue
            if got.get("unchanged"):
                continue
            entry["files"] += 1
            entry["imported"] += int(got.get("imported") or 0)
        report["imported"] += entry["imported"]
        report["files"] += entry["files"]
        report["sources"].append(entry)
    return report
