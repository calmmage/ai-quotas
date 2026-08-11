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

DEFAULT_DATA_DIR = Path.home() / ".local" / "share" / "ai-quotas"
DEFAULT_SAMPLES_NAME = "samples.jsonl"


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


def extra_adapters_dir() -> Path | None:
    """Optional directory of drop-in private adapters (``*.py`` with ``snapshot``)."""
    raw = os.environ.get(ENV_EXTRA_ADAPTERS)
    if not raw or not raw.strip():
        return None
    return Path(raw).expanduser()


def plots_dir() -> Path:
    """Runtime plot output directory: ``<data_dir>/plots`` (not committed)."""
    return data_dir() / "plots"
