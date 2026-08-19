"""Single path-resolution module for sample storage.

Both the reader (CLI / lib) and the writer (collector) resolve samples through
this module so env overrides always apply to both sides.

Precedence:
  1. explicit ``path`` argument (call site)
  2. env ``AI_QUOTAS_SAMPLES`` (full file path)
  3. env ``AI_QUOTAS_DATA_DIR`` / ``<dir>/samples.jsonl``
  4. default ``~/.local/share/ai-quotas/samples.jsonl``
"""

from __future__ import annotations

import os
from pathlib import Path

ENV_SAMPLES = "AI_QUOTAS_SAMPLES"
ENV_DATA_DIR = "AI_QUOTAS_DATA_DIR"
ENV_EXTRA_ADAPTERS = "AI_QUOTAS_EXTRA_ADAPTERS"
ENV_SPEND = "AI_QUOTAS_SPEND"
ENV_AGENTIC_STEP_JOBS = "AGENTIC_STEP_JOBS"

DEFAULT_DATA_DIR = Path.home() / ".local" / "share" / "ai-quotas"
DEFAULT_SAMPLES_NAME = "samples.jsonl"
DEFAULT_SPEND_NAME = "spend.jsonl"
DEFAULT_SPEND_CURSOR_NAME = "spend-cursor.json"
DEFAULT_AGENTIC_STEP_JOBS = (
    Path.home() / ".local" / "share" / "agentic-step" / "jobs.jsonl"
)


def data_dir() -> Path:
    """Directory that holds samples.jsonl (and optional future artifacts)."""
    raw = os.environ.get(ENV_DATA_DIR)
    if raw and raw.strip():
        return Path(raw).expanduser()
    return DEFAULT_DATA_DIR


def samples_path(override: str | Path | None = None) -> Path:
    """Resolve the samples.jsonl path.

    ``override`` wins when provided (CLI ``--samples``, library call).
    """
    if override is not None:
        return Path(override).expanduser()
    raw = os.environ.get(ENV_SAMPLES)
    if raw and raw.strip():
        return Path(raw).expanduser()
    return data_dir() / DEFAULT_SAMPLES_NAME


def spend_path(override: str | Path | None = None) -> Path:
    """Resolve spend.jsonl (session token/$ harvest). Sibling of samples by default.

    Precedence: explicit override → env AI_QUOTAS_SPEND → <samples parent>/spend.jsonl.
    """
    if override is not None:
        return Path(override).expanduser()
    raw = os.environ.get(ENV_SPEND)
    if raw and raw.strip():
        return Path(raw).expanduser()
    return samples_path().with_name(DEFAULT_SPEND_NAME)


def spend_cursor_path(spend: str | Path | None = None) -> Path:
    """Incremental harvest cursor next to the spend file."""
    return spend_path(spend).with_name(DEFAULT_SPEND_CURSOR_NAME)


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
