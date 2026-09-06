"""SQLite-backed runtime storage with bounded JSONL compatibility/imports."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = 3
_SQLITE_SUFFIXES = {".db", ".sqlite", ".sqlite3"}


def is_database(path: str | Path) -> bool:
    return Path(path).suffix.lower() in _SQLITE_SUFFIXES


def _json(row: dict[str, Any]) -> str:
    return json.dumps(row, ensure_ascii=False, separators=(",", ":"))


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    created = not path.exists()
    conn = sqlite3.connect(path, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    _ensure_schema(conn)
    if created:
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    return conn


@contextmanager
def _database(path: Path):
    conn = _connect(path)
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def _ensure_schema(conn: sqlite3.Connection) -> None:
    current = int(conn.execute("PRAGMA user_version").fetchone()[0])
    if current > SCHEMA_VERSION:
        raise RuntimeError(
            f"database schema {current} is newer than supported {SCHEMA_VERSION}"
        )
    if current < 1:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS quota_samples (
                id INTEGER PRIMARY KEY,
                ts TEXT,
                provider TEXT,
                quota_window TEXT,
                used_percent REAL,
                resets_at TEXT,
                plan TEXT,
                status TEXT,
                reason TEXT,
                limit_value REAL,
                used_value REAL,
                payload_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS quota_samples_lookup
                ON quota_samples(provider, quota_window, ts);
            CREATE INDEX IF NOT EXISTS quota_samples_ts
                ON quota_samples(ts);

            CREATE TABLE IF NOT EXISTS spend_turns (
                id INTEGER PRIMARY KEY,
                turn_key TEXT NOT NULL UNIQUE,
                ts TEXT,
                provider TEXT,
                session_id TEXT,
                turn_id TEXT,
                total_tokens INTEGER,
                cost_usd REAL,
                payload_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS spend_turns_lookup
                ON spend_turns(provider, session_id, ts);
            CREATE INDEX IF NOT EXISTS spend_turns_ts
                ON spend_turns(ts);

            CREATE TABLE IF NOT EXISTS harvest_files (
                path TEXT PRIMARY KEY,
                mtime_ns INTEGER NOT NULL,
                size INTEGER NOT NULL,
                n_new INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT
            );

            CREATE TABLE IF NOT EXISTS app_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS legacy_import_rows (
                kind TEXT NOT NULL,
                source_path TEXT NOT NULL,
                line_number INTEGER NOT NULL,
                row_sha256 TEXT NOT NULL,
                imported_at TEXT NOT NULL,
                PRIMARY KEY(kind, source_path, line_number)
            );
            """
        )
    if current < 2:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS reset_credits (
                id INTEGER PRIMARY KEY,
                ts TEXT,
                provider TEXT,
                credit_id TEXT,
                status TEXT,
                granted_at TEXT,
                expires_at TEXT,
                payload_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS reset_credits_lookup
                ON reset_credits(provider, credit_id, ts);
            CREATE INDEX IF NOT EXISTS reset_credits_ts
                ON reset_credits(ts);
            """
        )
    if current < 3:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS boosts (
                id INTEGER PRIMARY KEY,
                boost_key TEXT NOT NULL UNIQUE,
                provider TEXT,
                quota_window TEXT,
                percent REAL,
                starts_at TEXT,
                ends_at TEXT,
                first_seen_ts TEXT,
                last_seen_ts TEXT,
                raw_text TEXT,
                payload_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS boosts_lookup
                ON boosts(provider, quota_window, last_seen_ts);
            """
        )
    if current < SCHEMA_VERSION:
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.execute(
            "INSERT OR REPLACE INTO app_metadata(key, value) VALUES (?, ?)",
            ("schema_version", str(SCHEMA_VERSION)),
        )
        conn.commit()


def ensure_database(path: str | Path) -> Path:
    p = Path(path).expanduser()
    with _database(p):
        pass
    return p


def schema_version(path: str | Path) -> int:
    p = Path(path).expanduser()
    if not p.is_file():
        return 0
    with _database(p) as conn:
        return int(conn.execute("PRAGMA user_version").fetchone()[0])


def integrity_check(path: str | Path) -> str:
    p = Path(path).expanduser()
    if not p.is_file():
        return "missing"
    with _database(p) as conn:
        return str(conn.execute("PRAGMA integrity_check").fetchone()[0])


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                out.append(obj)
    return out


def _load_table(path: Path, table: str) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    with _database(path) as conn:
        rows = conn.execute(f"SELECT payload_json FROM {table} ORDER BY id").fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        try:
            payload = json.loads(row[0])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            out.append(payload)
    return out


def load_samples(path: str | Path) -> list[dict[str, Any]]:
    p = Path(path).expanduser()
    return _load_table(p, "quota_samples") if is_database(p) else _load_jsonl(p)


def load_spend(path: str | Path) -> list[dict[str, Any]]:
    p = Path(path).expanduser()
    return _load_table(p, "spend_turns") if is_database(p) else _load_jsonl(p)


RESET_CREDITS_JSONL_SUFFIX = ".reset-credits.jsonl"


def reset_credits_jsonl_path(samples: Path) -> Path:
    """Legacy JSONL sibling for reset-credit rows (fixtures / --samples)."""
    return samples.with_name(samples.stem + RESET_CREDITS_JSONL_SUFFIX)


def load_reset_credits(path: str | Path) -> list[dict[str, Any]]:
    p = Path(path).expanduser()
    if is_database(p):
        return _load_table(p, "reset_credits")
    return _load_jsonl(reset_credits_jsonl_path(p))


_RESET_INSERT = """
    INSERT INTO reset_credits(
        ts, provider, credit_id, status, granted_at, expires_at, payload_json
    ) VALUES (?, ?, ?, ?, ?, ?, ?)
"""


def append_reset_credits(path: str | Path, rows: Iterable[dict[str, Any]]) -> int:
    p = Path(path).expanduser()
    materialized = list(rows)
    if not materialized:
        return 0
    if not is_database(p):
        target = reset_credits_jsonl_path(p)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as fh:
            for row in materialized:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        return len(materialized)
    with _database(p) as conn:
        conn.executemany(
            _RESET_INSERT,
            (
                (
                    row.get("ts"),
                    row.get("provider"),
                    row.get("credit_id"),
                    row.get("status"),
                    row.get("granted_at"),
                    row.get("expires_at"),
                    _json(row),
                )
                for row in materialized
            ),
        )
    return len(materialized)


BOOSTS_JSONL_SUFFIX = ".boosts.jsonl"


def boosts_jsonl_path(samples: Path) -> Path:
    """Legacy JSONL sibling for boost rows (fixtures / --samples)."""
    return samples.with_name(samples.stem + BOOSTS_JSONL_SUFFIX)


def load_boosts(path: str | Path) -> list[dict[str, Any]]:
    p = Path(path).expanduser()
    if is_database(p):
        return _load_table(p, "boosts")
    return _load_jsonl(boosts_jsonl_path(p))


def _boost_payload(row: dict[str, Any], *, ts: str) -> dict[str, Any]:
    payload = dict(row)
    payload.setdefault("kind", "boost")
    payload["ts"] = ts
    if "window" not in payload and payload.get("quota_window"):
        payload["window"] = payload["quota_window"]
    return payload


def upsert_boosts(path: str | Path, rows: Iterable[dict[str, Any]]) -> int:
    """Insert or extend last_seen_ts. Returns number of rows touched (insert+update)."""
    from ai_quotas.boosts import identity_key

    p = Path(path).expanduser()
    materialized = [row for row in rows if isinstance(row, dict)]
    if not materialized:
        return 0
    if not is_database(p):
        target = boosts_jsonl_path(p)
        existing = _load_jsonl(target)
        by_key: dict[str, dict[str, Any]] = {}
        for row in existing:
            by_key[identity_key(row)] = row
        touched = 0
        for row in materialized:
            ts = str(row.get("ts") or "")
            payload = _boost_payload(row, ts=ts)
            key = identity_key(payload)
            prev = by_key.get(key)
            if prev is None:
                payload["first_seen_ts"] = payload.get("first_seen_ts") or ts
                payload["last_seen_ts"] = ts
                payload.setdefault("starts_at", payload["first_seen_ts"])
                by_key[key] = payload
                touched += 1
            else:
                prev["last_seen_ts"] = ts
                if payload.get("raw_text"):
                    prev["raw_text"] = payload["raw_text"]
                prev["ts"] = ts
                touched += 1
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8") as fh:
            for row in by_key.values():
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        return touched
    touched = 0
    with _database(p) as conn:
        for row in materialized:
            ts = str(row.get("ts") or "")
            payload = _boost_payload(row, ts=ts)
            key = identity_key(payload)
            provider = payload.get("provider")
            window = payload.get("window") or payload.get("quota_window")
            percent = payload.get("percent")
            ends_at = payload.get("ends_at")
            raw_text = payload.get("raw_text")
            existing = conn.execute(
                "SELECT first_seen_ts, starts_at, payload_json FROM boosts WHERE boost_key = ?",
                (key,),
            ).fetchone()
            if existing is None:
                first = payload.get("first_seen_ts") or ts
                starts = payload.get("starts_at") or first
                payload["first_seen_ts"] = first
                payload["last_seen_ts"] = ts
                payload["starts_at"] = starts
                conn.execute(
                    """
                    INSERT INTO boosts(
                        boost_key, provider, quota_window, percent, starts_at,
                        ends_at, first_seen_ts, last_seen_ts, raw_text, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        key,
                        provider,
                        window,
                        percent,
                        starts,
                        ends_at,
                        first,
                        ts,
                        raw_text,
                        _json(payload),
                    ),
                )
            else:
                try:
                    prev_payload = json.loads(existing["payload_json"])
                except json.JSONDecodeError:
                    prev_payload = {}
                if not isinstance(prev_payload, dict):
                    prev_payload = {}
                prev_payload.update(payload)
                prev_payload["first_seen_ts"] = existing["first_seen_ts"]
                prev_payload["last_seen_ts"] = ts
                prev_payload["starts_at"] = existing["starts_at"] or existing["first_seen_ts"]
                if raw_text:
                    prev_payload["raw_text"] = raw_text
                conn.execute(
                    """
                    UPDATE boosts SET
                        last_seen_ts = ?,
                        raw_text = COALESCE(?, raw_text),
                        payload_json = ?
                    WHERE boost_key = ?
                    """,
                    (ts, raw_text, _json(prev_payload), key),
                )
            touched += 1
    return touched


def load_spend_keys(path: str | Path) -> set[str]:
    p = Path(path).expanduser()
    if not is_database(p):
        return {
            spend_turn_key(row)
            for row in _load_jsonl(p)
            if row.get("turn_id")
        }
    if not p.is_file():
        return set()
    with _database(p) as conn:
        return {
            str(row[0])
            for row in conn.execute("SELECT turn_key FROM spend_turns").fetchall()
        }


def _sample_values(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        row.get("ts"),
        row.get("provider"),
        row.get("window"),
        row.get("used_percent"),
        row.get("resets_at"),
        row.get("plan"),
        row.get("status"),
        row.get("reason"),
        row.get("limit"),
        row.get("used"),
        _json(row),
    )


_SAMPLE_INSERT = """
    INSERT INTO quota_samples(
        ts, provider, quota_window, used_percent, resets_at, plan, status,
        reason, limit_value, used_value, payload_json
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def append_samples(path: str | Path, rows: Iterable[dict[str, Any]]) -> int:
    p = Path(path).expanduser()
    materialized = list(rows)
    if not is_database(p):
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            for row in materialized:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        return len(materialized)
    with _database(p) as conn:
        conn.executemany(_SAMPLE_INSERT, (_sample_values(row) for row in materialized))
    return len(materialized)


def spend_turn_key(row: dict[str, Any]) -> str:
    return f"{row.get('provider')}\0{row.get('session_id')}\0{row.get('turn_id')}"


def _insert_spend(conn: sqlite3.Connection, row: dict[str, Any]) -> bool:
    before = conn.total_changes
    conn.execute(
        """
        INSERT OR IGNORE INTO spend_turns(
            turn_key, ts, provider, session_id, turn_id, total_tokens,
            cost_usd, payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            spend_turn_key(row),
            row.get("ts"),
            row.get("provider"),
            row.get("session_id"),
            row.get("turn_id"),
            row.get("total_tokens"),
            row.get("cost_usd"),
            _json(row),
        ),
    )
    return conn.total_changes > before


def append_spend(path: str | Path, rows: Iterable[dict[str, Any]]) -> int:
    p = Path(path).expanduser()
    materialized = list(rows)
    if not is_database(p):
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            for row in materialized:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        return len(materialized)
    added = 0
    with _database(p) as conn:
        for row in materialized:
            added += int(_insert_spend(conn, row))
    return added


def load_harvest_cursor(path: str | Path) -> dict[str, Any]:
    p = Path(path).expanduser()
    if not is_database(p):
        if not p.is_file():
            return {"files": {}}
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"files": {}}
        return raw if isinstance(raw, dict) else {"files": {}}
    if not p.is_file():
        return {"files": {}}
    with _database(p) as conn:
        rows = conn.execute(
            "SELECT path, mtime_ns, size, n_new FROM harvest_files"
        ).fetchall()
        updated = conn.execute(
            "SELECT value FROM app_metadata WHERE key = 'harvest_updated_at'"
        ).fetchone()
    return {
        "files": {
            str(row["path"]): {
                "mtime_ns": int(row["mtime_ns"]),
                "size": int(row["size"]),
                "n_new": int(row["n_new"]),
            }
            for row in rows
        },
        "updated_at": str(updated[0]) if updated else None,
        "dest": str(p),
    }


def save_harvest_cursor(path: str | Path, cursor: dict[str, Any]) -> None:
    p = Path(path).expanduser()
    if not is_database(p):
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(cursor, ensure_ascii=False) + "\n", encoding="utf-8")
        tmp.replace(p)
        return
    with _database(p) as conn:
        for source, raw in (cursor.get("files") or {}).items():
            if not isinstance(raw, dict):
                continue
            conn.execute(
                """
                INSERT INTO harvest_files(path, mtime_ns, size, n_new, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                    mtime_ns=excluded.mtime_ns,
                    size=excluded.size,
                    n_new=excluded.n_new,
                    updated_at=excluded.updated_at
                """,
                (
                    str(source),
                    int(raw.get("mtime_ns") or 0),
                    int(raw.get("size") or 0),
                    int(raw.get("n_new") or 0),
                    cursor.get("updated_at"),
                ),
            )
        if cursor.get("updated_at"):
            conn.execute(
                "INSERT OR REPLACE INTO app_metadata(key, value) VALUES (?, ?)",
                ("harvest_updated_at", str(cursor["updated_at"])),
            )


def fingerprint(path: str | Path, *, kind: str = "samples") -> tuple[Any, ...] | None:
    p = Path(path).expanduser()
    if not p.is_file():
        return None
    if not is_database(p):
        stat = p.stat()
        return ("file", int(stat.st_mtime_ns), int(stat.st_size))
    table = "quota_samples" if kind == "samples" else "spend_turns"
    with _database(p) as conn:
        row = conn.execute(f"SELECT count(*), max(id) FROM {table}").fetchone()
        if kind == "samples":
            # reset-credit / boost rows land a moment after the quota rows of
            # the same tick; the dash must regenerate for them too.
            extra = conn.execute("SELECT count(*), max(id) FROM reset_credits").fetchone()
            boosts = conn.execute("SELECT count(*), max(id) FROM boosts").fetchone()
            return (
                "sqlite",
                int(row[0]),
                int(row[1] or 0),
                int(extra[0]),
                int(extra[1] or 0),
                int(boosts[0]),
                int(boosts[1] or 0),
            )
    return ("sqlite", int(row[0]), int(row[1] or 0))


def _legacy_seen(
    conn: sqlite3.Connection,
    *,
    kind: str,
    source: str,
    line_number: int,
    row_sha256: str,
) -> str:
    existing = conn.execute(
        """
        SELECT row_sha256 FROM legacy_import_rows
        WHERE kind = ? AND source_path = ? AND line_number = ?
        """,
        (kind, source, line_number),
    ).fetchone()
    if existing is None:
        return "new"
    return "same" if str(existing[0]) == row_sha256 else "changed"


def import_jsonl(
    database: str | Path,
    source: str | Path,
    *,
    kind: str,
) -> dict[str, Any]:
    if kind not in {"samples", "spend"}:
        raise ValueError("kind must be 'samples' or 'spend'")
    db = Path(database).expanduser()
    src = Path(source).expanduser().resolve()
    report: dict[str, Any] = {
        "kind": kind,
        "source": str(src),
        "imported": 0,
        "skipped": 0,
        "rejected": 0,
    }
    if not src.is_file():
        report["missing"] = True
        return report
    imported_at = _now_iso()
    with _database(db) as conn, src.open("rb") as fh:
        for line_number, raw_line in enumerate(fh, 1):
            stripped = raw_line.strip()
            if not stripped:
                continue
            digest = hashlib.sha256(stripped).hexdigest()
            seen = _legacy_seen(
                conn,
                kind=kind,
                source=str(src),
                line_number=line_number,
                row_sha256=digest,
            )
            if seen == "same":
                report["skipped"] += 1
                continue
            if seen == "changed":
                report["rejected"] += 1
                continue
            try:
                obj = json.loads(stripped)
            except (UnicodeDecodeError, json.JSONDecodeError):
                report["rejected"] += 1
                continue
            if not isinstance(obj, dict):
                report["rejected"] += 1
                continue
            if kind == "samples":
                conn.execute(_SAMPLE_INSERT, _sample_values(obj))
                inserted = True
            else:
                inserted = _insert_spend(conn, obj)
            conn.execute(
                """
                INSERT INTO legacy_import_rows(
                    kind, source_path, line_number, row_sha256, imported_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (kind, str(src), line_number, digest, imported_at),
            )
            report["imported" if inserted else "skipped"] += 1
    return report


def import_cursor(database: str | Path, source: str | Path) -> dict[str, Any]:
    src = Path(source).expanduser().resolve()
    report: dict[str, Any] = {
        "kind": "cursor",
        "source": str(src),
        "imported": 0,
        "updated": 0,
        "skipped": 0,
    }
    if not src.is_file():
        report["missing"] = True
        return report
    cursor = load_harvest_cursor(src)
    files = cursor.get("files") or {}
    existing = load_harvest_cursor(database).get("files") or {}
    if isinstance(files, dict) and isinstance(existing, dict):
        for path, value in files.items():
            if path not in existing:
                report["imported"] += 1
            elif existing[path] == value:
                report["skipped"] += 1
            else:
                report["updated"] += 1
    save_harvest_cursor(database, cursor)
    return report


def row_counts(path: str | Path) -> dict[str, int]:
    p = Path(path).expanduser()
    if not p.is_file() or not is_database(p):
        return {
            "samples": 0,
            "spend": 0,
            "harvest_files": 0,
            "reset_credits": 0,
            "boosts": 0,
        }
    with _database(p) as conn:
        return {
            "samples": int(conn.execute("SELECT count(*) FROM quota_samples").fetchone()[0]),
            "spend": int(conn.execute("SELECT count(*) FROM spend_turns").fetchone()[0]),
            "harvest_files": int(conn.execute("SELECT count(*) FROM harvest_files").fetchone()[0]),
            "reset_credits": int(conn.execute("SELECT count(*) FROM reset_credits").fetchone()[0]),
            "boosts": int(conn.execute("SELECT count(*) FROM boosts").fetchone()[0]),
        }
