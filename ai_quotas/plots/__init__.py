"""Plot pipeline for ai-quotas (optional: ``ai-quotas[plot]``).

Public surface:

- ``prepare`` / ``is_reset`` / ``classify_money`` / money helpers — pure data prep
- ``generate_plots`` — write multi-vendor plotly + uplot dashboards
- ``default_plots_dir`` — runtime output under the package data dir
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "classify_money",
    "default_plots_dir",
    "format_money_report",
    "generate_plots",
    "is_reset",
    "money_summary",
    "prepare",
]


def __getattr__(name: str) -> Any:
    if name in {
        "classify_money",
        "default_plots_dir",
        "format_money_report",
        "is_reset",
        "money_summary",
        "prepare",
    }:
        from ai_quotas.plots import prep as _prep

        return getattr(_prep, name)
    if name == "generate_plots":
        from ai_quotas.plots.generate import generate_plots

        return generate_plots
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
