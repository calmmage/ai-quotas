"""Incremental scan of this Mac's AI transcripts. Read-only on the logs."""

from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import unquote

from ai_quotas.speed import claude as claude_mod
from ai_quotas.speed import codex as codex_mod
from ai_quotas.speed import grok as grok_mod
from ai_quotas.speed import muse as muse_mod
from ai_quotas.speed.store import (
    connect,
    delete_source,
    load_file_state,
    save_file_state,
    upsert_rows,
)

_CODEX_ID = re.compile(
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
    re.I,
)
_CHUNK = 4 * 1024 * 1024

# Checked 4 Nov 2026. Included only when a per-response token count and a clock exist.
NO_TIMING = (
    {
        "harness": "antigravity",
        "reason": "conversation db has model ids and no per-response token timestamps",
    },
    {
        "harness": "claude-desktop-cowork",
        "reason": "claude-code-sessions JSON is metadata (no usage tokens)",
    },
    {
        "harness": "gateway",
        "reason": "LiteLLM spend logs live in remote Postgres; not opened from this Mac",
    },
)


class Progress:
    """stderr progress. Plain lines when stdout is a pipe or launchd."""

    def __init__(self, total_files: int, total_bytes: int, enabled: bool) -> None:
        self.total_files = total_files
        self.total_bytes = max(total_bytes, 1)
        self.enabled = enabled
        self.tty = enabled and sys.stderr.isatty()
        self.started = time.monotonic()
        self.last = 0.0
        self.files_done = 0
        self.bytes_done = 0
        self.rows = 0

    def update(self, *, files_done: int, bytes_done: int, rows: int, name: str) -> None:
        if not self.enabled:
            return
        self.files_done = files_done
        self.bytes_done = bytes_done
        self.rows = rows
        now = time.monotonic()
        if self.tty:
            if now - self.last < 0.1 and files_done < self.total_files:
                return
        elif now - self.last < 5 and files_done < self.total_files:
            return
        self.last = now
        self._paint(name)

    def close(self, name: str = "done") -> None:
        if not self.enabled:
            return
        self._paint(name)
        if self.tty:
            sys.stderr.write("\n")
        else:
            sys.stderr.write("\n")
        sys.stderr.flush()

    def _paint(self, name: str) -> None:
        elapsed = max(time.monotonic() - self.started, 0.001)
        frac = min(1.0, self.bytes_done / self.total_bytes)
        rate = self.bytes_done / elapsed
        remain = (self.total_bytes - self.bytes_done) / rate if rate > 0 else 0
        text = (
            f"speed {self.files_done}/{self.total_files} files"
            f"  {frac * 100:5.1f}%"
            f"  rows {self.rows}"
            f"  {elapsed:6.1f}s"
            f"  eta {remain:5.0f}s"
            f"  {name}"
        )
        if self.tty:
            sys.stderr.write("\r" + text[:180] + "   ")
        else:
            sys.stderr.write(text[:180] + "\n")
        sys.stderr.flush()


def _home() -> Path:
    return Path.home()


def _inode(path: Path) -> tuple[int, int] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_dev, st.st_ino)


def _dedupe(paths: Iterable[Path]) -> list[Path]:
    seen: set[tuple[int, int]] = set()
    kept: list[Path] = []
    for path in paths:
        if not path.is_file():
            continue
        if "ai-chats" in path.parts and "calm" in path.parts:
            continue
        ident = _inode(path)
        if ident is None or ident in seen:
            continue
        seen.add(ident)
        kept.append(path)
    return kept


def discover() -> list[tuple[str, Path]]:
    """Harness files on this Mac. Hardlinks count once. The other Mac's mirror is skipped."""
    home = _home()
    jobs: list[tuple[str, Path]] = []
    claude_root = home / ".claude" / "projects"
    if claude_root.is_dir():
        jobs.extend(("claude", path) for path in _dedupe(claude_root.rglob("*.jsonl")))
    codex_paths: list[Path] = []
    for root in (
        home / ".codex" / "sessions",
        home / ".codex" / "archived_sessions",
        home / "Library" / "Application Support" / "Orca",
    ):
        if root.is_dir():
            codex_paths.extend(root.rglob("rollout-*.jsonl"))
            codex_paths.extend(root.rglob("rollout-*.jsonl.zst"))
    jobs.extend(("codex", path) for path in _dedupe(codex_paths))
    grok_root = home / ".grok" / "sessions"
    if grok_root.is_dir():
        jobs.extend(("grok", path) for path in _dedupe(grok_root.rglob("updates.jsonl")))
    muse_root = home / ".local" / "share" / "muse" / "sessions"
    if muse_root.is_dir():
        jobs.extend(("muse", path) for path in _dedupe(muse_root.rglob("session.jsonl")))
    return jobs


def _session_from_name(path: Path) -> str:
    match = _CODEX_ID.search(path.name)
    return match.group(1) if match else path.stem


def _grok_cwd(path: Path) -> str | None:
    # ~/.grok/sessions/<percent-encoded cwd>/<session>/updates.jsonl
    try:
        return unquote(path.parents[1].name)
    except IndexError:
        return None


def _feed_for(harness: str, path: Path):
    if harness == "claude":
        return claude_mod.ClaudeFeed()
    if harness == "codex":
        return codex_mod.CodexFeed(_session_from_name(path))
    if harness == "grok":
        return grok_mod.GrokFeed(path.parent.name, _grok_cwd(path))
    if harness == "muse":
        return muse_mod.MuseFeed(path.parent.name)
    raise ValueError(harness)


def _interesting(harness: str, line: str) -> bool:
    if harness == "claude":
        return claude_mod.interesting(line)
    if harness == "codex":
        return codex_mod.interesting(line)
    if harness == "grok":
        return grok_mod.interesting_update(line)
    if harness == "muse":
        return muse_mod.interesting(line)
    return False


def _loads(line: str) -> dict | None:
    if len(line) > 4_000_000:
        return None
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def _consume_file(
    path: Path,
    harness: str,
    feed: Any,
    offset: int,
    on_rows: Callable[[list[dict], int, dict], None],
) -> tuple[int, int]:
    """Read new complete lines. Returns (new_offset, rows_emitted)."""
    emitted = 0
    pos = offset
    try:
        handle = path.open("rb")
    except OSError:
        return offset, 0
    with handle:
        handle.seek(offset)
        buf = b""
        while True:
            block = handle.read(_CHUNK)
            if not block:
                break
            buf += block
            cut = buf.rfind(b"\n")
            if cut < 0:
                if len(buf) > 32 * 1024 * 1024:
                    pos += len(buf)
                    buf = b""
                continue
            whole = buf[: cut + 1]
            buf = buf[cut + 1 :]
            batch: list[dict] = []
            for raw in whole.split(b"\n"):
                if not raw:
                    continue
                line = raw.decode("utf-8", "replace")
                if not _interesting(harness, line):
                    continue
                obj = _loads(line)
                if obj is None:
                    continue
                batch.extend(row for row in feed.feed(obj) if row)
            pos += len(whole)
            on_rows(batch, pos, feed.dump())
            emitted += len(batch)
    return pos, emitted


def _scan_grok_events(path: Path, feed: Any, conn, node_unused: str) -> None:
    del node_unused
    events = path.parent / "events.jsonl"
    if not events.is_file():
        return
    saved = load_file_state(conn, str(events))
    offset = int(saved["offset"]) if saved else 0
    try:
        size = events.stat().st_size
    except OSError:
        return
    if size < offset:
        offset = 0
    pos = offset
    try:
        handle = events.open("rb")
    except OSError:
        return
    with handle:
        handle.seek(offset)
        buf = b""
        while True:
            block = handle.read(_CHUNK)
            if not block:
                break
            buf += block
            cut = buf.rfind(b"\n")
            if cut < 0:
                continue
            whole = buf[: cut + 1]
            buf = buf[cut + 1 :]
            for raw in whole.split(b"\n"):
                if not raw:
                    continue
                line = raw.decode("utf-8", "replace")
                if not grok_mod.interesting_event(line):
                    continue
                obj = _loads(line)
                if obj is None:
                    continue
                obj = dict(obj)
                obj["_kind"] = "event"
                feed.feed(obj)
            pos += len(whole)
    try:
        st = events.stat()
    except OSError:
        return
    save_file_state(
        conn,
        path=str(events),
        mtime_ns=int(st.st_mtime_ns),
        size=int(st.st_size),
        offset=pos,
        state={"companion": str(path)},
    )


def _scan_zst(conn, path: Path, st: os.stat_result, node_name: str) -> int | None:
    """Decompress an archived rollout once. Cursor stores the compressed size.

    Returns None when the compressed file is unchanged since the last full read.
    """
    source = str(path)
    saved = load_file_state(conn, source)
    if (
        saved
        and int(saved["mtime_ns"]) == int(st.st_mtime_ns)
        and int(saved["size"]) == int(st.st_size)
        and int(saved["offset"]) == int(st.st_size)
    ):
        return None
    delete_source(conn, source)
    feed = codex_mod.CodexFeed(_session_from_name(path))
    written = 0
    proc = subprocess.Popen(
        ["zstd", "-dc", "-q", str(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    assert proc.stdout is not None
    buf = b""
    batch: list[dict] = []

    def flush() -> None:
        nonlocal written
        if not batch:
            return
        written += upsert_rows(conn, batch, node=node_name, source_file=source)
        batch.clear()

    while True:
        block = proc.stdout.read(_CHUNK)
        if not block:
            break
        buf += block
        cut = buf.rfind(b"\n")
        if cut < 0:
            continue
        whole, buf = buf[: cut + 1], buf[cut + 1 :]
        for raw in whole.split(b"\n"):
            if not raw:
                continue
            line = raw.decode("utf-8", "replace")
            if not codex_mod.interesting(line):
                continue
            obj = _loads(line)
            if obj is None:
                continue
            batch.extend(row for row in feed.feed(obj) if row)
        flush()
        conn.commit()
    rest = proc.wait()
    if rest != 0:
        return written
    batch.extend(row for row in feed.provisional() if row)
    flush()
    save_file_state(
        conn,
        path=source,
        mtime_ns=int(st.st_mtime_ns),
        size=int(st.st_size),
        offset=int(st.st_size),
        state={"feed": feed.dump()},
    )
    conn.commit()
    return written


def scan(
    database: str | Path,
    *,
    files: list[tuple[str, Path]] | None = None,
    progress: bool = True,
    node: str | None = None,
) -> dict[str, Any]:
    """Scan transcripts into ``response_speed``. Safe to resume after a stop."""
    path = Path(database).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.parent / "speed-scan.lock"
    lock = lock_path.open("a")
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return {"already_running": True, "database": str(path), "rows": 0, "files": 0}
    node_name = node or socket.gethostname()
    jobs = files if files is not None else discover()
    total_bytes = 0
    usable: list[tuple[str, Path, os.stat_result]] = []
    for harness, item in jobs:
        try:
            st = item.stat()
        except OSError:
            continue
        usable.append((harness, item, st))
        total_bytes += st.st_size
    bar = Progress(len(usable), total_bytes, progress)
    started = time.monotonic()
    written = 0
    scanned = 0
    skipped = 0
    zst_skipped = 0
    conn = connect(path)
    try:
        bytes_done = 0
        for index, (harness, item, st) in enumerate(usable, start=1):
            source = str(item)
            bar.update(files_done=index - 1, bytes_done=bytes_done, rows=written, name=item.name)
            if item.suffix == ".zst":
                if shutil.which("zstd") is None:
                    zst_skipped += 1
                else:
                    added = _scan_zst(conn, item, st, node_name)
                    if added is None:
                        skipped += 1
                    else:
                        written += added
                        scanned += 1
                bytes_done += st.st_size
                bar.update(files_done=index, bytes_done=bytes_done, rows=written, name=item.name)
                continue
            saved = load_file_state(conn, source)
            mtime_ns = int(st.st_mtime_ns)
            size = int(st.st_size)
            # Unchanged and fully read: skip. A stopped scan keeps a short offset
            # on the same mtime and size, so that case resumes. A same-size rewrite
            # (new mtime) or a shrink starts over and drops the old rows.
            fully_read = (
                saved is not None
                and int(saved["mtime_ns"]) == mtime_ns
                and int(saved["size"]) == size
                and int(saved["offset"]) >= size
            )
            same_file_partial = (
                saved is not None
                and int(saved["mtime_ns"]) == mtime_ns
                and int(saved["size"]) == size
                and int(saved["offset"]) < size
            )
            appended = saved is not None and size > int(saved["size"]) and int(saved["offset"]) <= size
            if fully_read:
                skipped += 1
                bytes_done += st.st_size
                bar.update(files_done=index, bytes_done=bytes_done, rows=written, name=item.name)
                continue
            if same_file_partial or appended:
                offset = int(saved["offset"])
                state = saved["state"]
            else:
                if saved is not None:
                    delete_source(conn, source)
                    conn.commit()
                offset = 0
                state = {}
            feed = _feed_for(harness, item)
            feed.load(state.get("feed") if isinstance(state, dict) else {})
            if harness == "grok":
                _scan_grok_events(item, feed, conn, node_name)

            def on_rows(batch: list[dict], pos: int, feed_state: dict, _item=item, _st_size=st.st_size) -> None:
                nonlocal written
                if batch:
                    written += upsert_rows(conn, batch, node=node_name, source_file=str(_item))
                save_file_state(
                    conn,
                    path=str(_item),
                    mtime_ns=int(_item.stat().st_mtime_ns),
                    size=int(_item.stat().st_size),
                    offset=pos,
                    state={"feed": feed_state},
                )
                conn.commit()

            new_offset, _emitted = _consume_file(item, harness, feed, offset, on_rows)
            tail = [row for row in feed.provisional() if row]
            if tail:
                written += upsert_rows(conn, tail, node=node_name, source_file=source)
            save_file_state(
                conn,
                path=source,
                mtime_ns=int(item.stat().st_mtime_ns),
                size=int(item.stat().st_size),
                offset=new_offset,
                state={"feed": feed.dump()},
            )
            conn.commit()
            scanned += 1
            bytes_done += st.st_size
            bar.update(files_done=index, bytes_done=bytes_done, rows=written, name=item.name)
        conn.commit()
    finally:
        conn.close()
        bar.close()
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()
    return {
        "already_running": False,
        "database": str(path),
        "node": node_name,
        "files": len(usable),
        "scanned_files": scanned,
        "skipped_unchanged": skipped,
        "zst_skipped": zst_skipped,
        "rows": written,
        "elapsed_s": round(time.monotonic() - started, 3),
        "no_timing": list(NO_TIMING),
    }



