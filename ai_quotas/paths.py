"""Single path-resolution module for ai-quotas runtime storage.

The live default is one SQLite database. JSONL paths remain accepted only as
explicit compatibility sources/targets for fixtures and bounded migration.

Precedence:
  1. explicit path argument
  2. env ``AI_QUOTAS_DATABASE``
  3. env ``AI_QUOTAS_DATA_DIR`` / ``ai-quotas.sqlite3``
  4. default ``~/.local/share/ai-quotas/ai-quotas.sqlite3``
"""

from __future__ import annotations

import os
from pathlib import Path

ENV_SAMPLES = "AI_QUOTAS_SAMPLES"
ENV_DATA_DIR = "AI_QUOTAS_DATA_DIR"
ENV_DATABASE = "AI_QUOTAS_DATABASE"
ENV_EXTRA_ADAPTERS = "AI_QUOTAS_EXTRA_ADAPTERS"
ENV_SPEND = "AI_QUOTAS_SPEND"
ENV_AGENTIC_STEP_JOBS = "AGENTIC_STEP_JOBS"
ENV_AFTER_REGEN = "AI_QUOTAS_AFTER_REGEN"  # dash: shell command after each regen

DEFAULT_DATA_DIR = Path.home() / ".local" / "share" / "ai-quotas"
DEFAULT_DATABASE_NAME = "ai-quotas.sqlite3"
DEFAULT_SAMPLES_NAME = "samples.jsonl"
DEFAULT_SPEND_NAME = "spend.jsonl"
DEFAULT_SPEND_CURSOR_NAME = "spend-cursor.json"
DEFAULT_AGENTIC_STEP_JOBS = (
    Path.home() / ".local" / "share" / "agentic-step" / "jobs.jsonl"
)


def data_dir() -> Path:
    """Directory that holds the database and generated runtime artifacts."""
    raw = os.environ.get(ENV_DATA_DIR)
    if raw and raw.strip():
        return Path(raw).expanduser()
    return DEFAULT_DATA_DIR


def database_path(override: str | Path | None = None) -> Path:
    """Resolve the authoritative SQLite database path."""
    if override is not None:
        return Path(override).expanduser()
    raw = os.environ.get(ENV_DATABASE)
    if raw and raw.strip():
        return Path(raw).expanduser()
    return data_dir() / DEFAULT_DATABASE_NAME


def samples_path(override: str | Path | None = None) -> Path:
    """Resolve quota storage, honoring the legacy JSONL override.

    New callers should use :func:`database_path`. This compatibility resolver
    keeps ``--samples`` and ``AI_QUOTAS_SAMPLES`` useful for fixtures/imports.
    """
    if override is not None:
        return Path(override).expanduser()
    raw = os.environ.get(ENV_SAMPLES)
    if raw and raw.strip():
        return Path(raw).expanduser()
    return database_path()


def spend_path(override: str | Path | None = None) -> Path:
    """Resolve session-spend storage.

    Explicit/env JSONL overrides remain compatible; otherwise spend uses the
    same authoritative SQLite database as quota samples.
    """
    if override is not None:
        return Path(override).expanduser()
    raw = os.environ.get(ENV_SPEND)
    if raw and raw.strip():
        return Path(raw).expanduser()
    legacy_samples = os.environ.get(ENV_SAMPLES)
    if legacy_samples and legacy_samples.strip():
        return Path(legacy_samples).expanduser().with_name(DEFAULT_SPEND_NAME)
    return database_path()


def spend_cursor_path(spend: str | Path | None = None) -> Path:
    """Resolve cursor storage (inside SQLite, or next to explicit JSONL)."""
    resolved = spend_path(spend)
    if resolved.suffix.lower() in {".db", ".sqlite", ".sqlite3"}:
        return resolved
    return resolved.with_name(DEFAULT_SPEND_CURSOR_NAME)


def agentic_step_jobs_path(override: str | Path | None = None) -> Path:
    """Resolve agentic_step jobs.jsonl (labels for THESE chats).

    Precedence: explicit override → env AGENTIC_STEP_JOBS →
    ``~/.local/share/agentic-step/jobs.jsonl``.
    Not a sibling of samples — the jobs file is owned by agentic_step.
    """
    if override is not None:
        return Path(override).expanduser()
    raw = os.environ.get(ENV_AGENTIC_STEP_JOBS)
    if raw and raw.strip():
        return Path(raw).expanduser()
    return DEFAULT_AGENTIC_STEP_JOBS


def extra_adapters_dir() -> Path | None:
    """Optional directory of drop-in private adapters (``*.py`` with ``snapshot``)."""
    raw = os.environ.get(ENV_EXTRA_ADAPTERS)
    if not raw or not raw.strip():
        return None
    return Path(raw).expanduser()


def plots_dir() -> Path:
    """Runtime plot output directory: ``<data_dir>/plots`` (not committed)."""
    return data_dir() / "plots"


def doctor_report() -> str:
    """Resolved paths plus the env vars that actually override them."""
    keys = (
        ENV_DATABASE,
        ENV_DATA_DIR,
        ENV_SAMPLES,
        ENV_SPEND,
        ENV_EXTRA_ADAPTERS,
        ENV_AGENTIC_STEP_JOBS,
        ENV_AFTER_REGEN,
    )
    lines = ["ai-quotas env (set vs unset):"]
    for key in keys:
        raw = os.environ.get(key)
        lines.append(f"  {key}={raw if raw else '(unset)'}")
    extra = extra_adapters_dir()
    lines.extend(
        [
            "resolved:",
            f"  database {database_path()}",
            f"  samples  {samples_path()}",
            f"  spend    {spend_path()}",
            f"  plots    {plots_dir()}",
            f"  jobs     {agentic_step_jobs_path()}",
            f"  extra    {extra if extra is not None else '(none)'}",
        ]
    )
    return "\n".join(lines)
