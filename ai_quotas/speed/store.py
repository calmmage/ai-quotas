"""Speed tables in the quota SQLite file.

The quota schema version stays put. A newer ``user_version`` would make the
already-running sampler refuse the live database. These tables are additive.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

_COLUMNS = (
    "response_key",
    "ts",
    "node",
    "harness",
    "provider",
    "model",
    "tier",
    "effort",
    "output_tokens",
    "reasoning_tokens",
    "seconds",
    "tokens_per_second",
    "session_id",
    "cwd",
    "source_file",
    "parallel_tools",
    "compaction",
)


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA journal_mode = WAL")
    ensure_tables(conn)
    return conn


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS response_speed (
            response_key TEXT PRIMARY KEY,
            ts TEXT NOT NULL,
            node TEXT NOT NULL,
            harness TEXT NOT NULL,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            tier TEXT NOT NULL,
            effort TEXT,
            output_tokens INTEGER,
            reasoning_tokens INTEGER,
            seconds REAL,
            tokens_per_second REAL,
            session_id TEXT,
            cwd TEXT,
            source_file TEXT,
            parallel_tools INTEGER NOT NULL DEFAULT 0,
            compaction INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS response_speed_ts
            ON response_speed(ts);
        CREATE INDEX IF NOT EXISTS response_speed_group
            ON response_speed(provider, model, tier, ts);
        CREATE INDEX IF NOT EXISTS response_speed_eligible
            ON response_speed(ts, provider, model, tier, tokens_per_second)
            WHERE output_tokens >= 20
              AND seconds >= 0.3
              AND parallel_tools = 0
              AND compaction = 0
              AND tokens_per_second IS NOT NULL;

        CREATE TABLE IF NOT EXISTS speed_scan_files (
            path TEXT PRIMARY KEY,
            mtime_ns INTEGER NOT NULL,
            size INTEGER NOT NULL,
            offset INTEGER NOT NULL,
            state_json TEXT NOT NULL,
            updated_at TEXT
        );
        """
    )


def load_file_state(conn: sqlite3.Connection, path: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT mtime_ns, size, offset, state_json FROM speed_scan_files WHERE path = ?",
        (path,),
    ).fetchone()
    if row is None:
        return None
    try:
        state = json.loads(row["state_json"])
    except json.JSONDecodeError:
        state = {}
    if not isinstance(state, dict):
        state = {}
    return {
        "mtime_ns": int(row["mtime_ns"]),
        "size": int(row["size"]),
        "offset": int(row["offset"]),
        "state": state,
    }


def save_file_state(
    conn: sqlite3.Connection,
    *,
    path: str,
    mtime_ns: int,
    size: int,
    offset: int,
    state: dict[str, Any],
) -> None:
    conn.execute(
        """
        INSERT INTO speed_scan_files(path, mtime_ns, size, offset, state_json, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(path) DO UPDATE SET
            mtime_ns = excluded.mtime_ns,
            size = excluded.size,
            offset = excluded.offset,
            state_json = excluded.state_json,
            updated_at = excluded.updated_at
        """,
        (
            path,
            int(mtime_ns),
            int(size),
            int(offset),
            json.dumps(state, separators=(",", ":")),
            datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        ),
    )


def delete_source(conn: sqlite3.Connection, source_file: str) -> None:
    conn.execute("DELETE FROM response_speed WHERE source_file = ?", (source_file,))


def upsert_rows(conn: sqlite3.Connection, rows: Iterable[dict[str, Any]], *, node: str, source_file: str) -> int:
    payload = []
    for row in rows:
        if not row or not row.get("response_key"):
            continue
        item = dict(row)
        item["node"] = node
        item["source_file"] = source_file
        payload.append(tuple(item.get(column) for column in _COLUMNS))
    if not payload:
        return 0
    conn.executemany(
        """
        INSERT INTO response_speed (
            response_key, ts, node, harness, provider, model, tier, effort,
            output_tokens, reasoning_tokens, seconds, tokens_per_second,
            session_id, cwd, source_file, parallel_tools, compaction
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(response_key) DO UPDATE SET
            ts = excluded.ts,
            node = excluded.node,
            harness = excluded.harness,
            provider = excluded.provider,
            model = excluded.model,
            tier = excluded.tier,
            effort = excluded.effort,
            output_tokens = MAX(response_speed.output_tokens, excluded.output_tokens),
            reasoning_tokens = CASE
                WHEN excluded.output_tokens >= COALESCE(response_speed.output_tokens, 0)
                THEN excluded.reasoning_tokens
                ELSE response_speed.reasoning_tokens
            END,
            seconds = excluded.seconds,
            tokens_per_second = excluded.tokens_per_second,
            session_id = excluded.session_id,
            cwd = excluded.cwd,
            source_file = excluded.source_file,
            parallel_tools = excluded.parallel_tools,
            compaction = excluded.compaction
        """,
        payload,
    )
    return len(payload)


def load_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    found = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'response_speed'"
    ).fetchone()
    if found is None:
        return []
    rows = conn.execute(
        """
        SELECT response_key, ts, node, harness, provider, model, tier, effort,
               output_tokens, reasoning_tokens, seconds, tokens_per_second,
               session_id, cwd, source_file, parallel_tools, compaction
        FROM response_speed
        ORDER BY ts
        """
    ).fetchall()
    return [dict(row) for row in rows]
