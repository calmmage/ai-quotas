"""ai-quotas — subscription quota sampling, trend math, and human CLI.

Library surface (importable):

    load_samples, latest_by_key, metrics_for_row, burn_metrics,
    table_rows, history, sample_now, verdicts
"""

from __future__ import annotations

from typing import Any

from ai_quotas.core import (
    burn_metrics,
    history,
    history_from_samples,
    latest_by_key,
    load_samples,
    metrics_for_row,
    verdicts,
)
from ai_quotas.paths import samples_path

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "burn_metrics",
    "history",
    "history_from_samples",
    "latest_by_key",
    "load_samples",
    "metrics_for_row",
    "sample_all",
    "sample_now",
    "samples_path",
    "table_rows",
    "verdicts",
]


def sample_all(*args: Any, **kwargs: Any):
    """Lazy re-export so ``python -m ai_quotas.collector`` is not pre-imported."""
    from ai_quotas.collector import sample_all as _sample_all

    return _sample_all(*args, **kwargs)


def sample_now(*args: Any, **kwargs: Any):
    from ai_quotas.collector import sample_now as _sample_now

    return _sample_now(*args, **kwargs)


def table_rows(*args: Any, **kwargs: Any):
    from ai_quotas.cli import table_rows as _table_rows

    return _table_rows(*args, **kwargs)
