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
from ai_quotas.paths import database_path, plots_dir, samples_path, spend_path

__version__ = "0.2.0"

__all__ = [
    "__version__",
    "burn_metrics",
    "database_path",
    "history",
    "history_from_samples",
    "latest_by_key",
    "load_samples",
    "metrics_for_row",
    "sample_all",
    "sample_now",
    "samples_path",
    "spend_path",
    "table_rows",
    "verdicts",
    # plots (lazy — requires ai-quotas[plot])
    "classify_money",
    "generate_plots",
    "is_reset",
    "money_summary",
    "plots_dir",
    "prepare_plots",
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


def prepare_plots(*args, **kwargs):
    """Lazy: load/prep samples for plotting (needs ``ai-quotas[plot]``)."""
    from ai_quotas.plots.prep import prepare

    return prepare(*args, **kwargs)


def generate_plots(*args, **kwargs):
    """Lazy: write multi-vendor dashboards (needs ``ai-quotas[plot]``)."""
    from ai_quotas.plots.generate import generate_plots as _gen

    return _gen(*args, **kwargs)


def is_reset(*args, **kwargs):
    from ai_quotas.plots.prep import is_reset as _is_reset

    return _is_reset(*args, **kwargs)


def classify_money(*args, **kwargs):
    from ai_quotas.plots.prep import classify_money as _cm

    return _cm(*args, **kwargs)


def money_summary(*args, **kwargs):
    from ai_quotas.plots.prep import money_summary as _ms

    return _ms(*args, **kwargs)
