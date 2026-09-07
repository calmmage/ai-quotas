"""Subscription-value estimates from reported plans and per-user settings.

The provider's plan label is not an invoice. Ambiguous tiers stay unpriced.
User overrides accept AI_QUOTAS_SUBSCRIPTIONS_JSON (environment or ~/.env),
or <data_dir>/subscriptions.json / an explicit AI_QUOTAS_SUBSCRIPTIONS file.
Prices are USD monthly list-price estimates, checked 07 Sep 2026:
https://learn.chatgpt.com/docs/pricing
https://support.claude.com/en/articles/11049762-choose-a-claude-plan
"""

from __future__ import annotations

import json
import math
import os
import shlex
from pathlib import Path
from typing import Any

from ai_quotas.paths import data_dir


def config_path() -> Path:
    return Path(os.environ.get("AI_QUOTAS_SUBSCRIPTIONS") or data_dir() / "subscriptions.json").expanduser()


ENV_JSON = "AI_QUOTAS_SUBSCRIPTIONS_JSON"


def dotenv_config() -> str | None:
    """Read only this app's non-secret setting from the user's ~/.env."""
    try:
        lines = (Path.home() / ".env").read_text().splitlines()
    except FileNotFoundError:
        return None
    for line in reversed(lines):
        text = line.strip().removeprefix("export ")
        if text.startswith(ENV_JSON + "="):
            values = shlex.split(text.split("=", 1)[1], comments=True)
            return values[0] if values else None
    return None


def save_dotenv(providers: dict) -> Path:
    path = Path.home() / ".env"
    text = path.read_text() if path.exists() else ""
    replacement = ENV_JSON + "=" + shlex.quote(json.dumps({"providers": providers}, separators=(",", ":")))
    lines = text.splitlines(keepends=True)
    matched = False
    for i, line in enumerate(lines):
        if line.strip().removeprefix("export ").startswith(ENV_JSON + "="):
            lines[i] = replacement + "\n"
            matched = True
    if not matched:
        if text and not text.endswith("\n"):
            lines.append("\n")
        lines.append(replacement + "\n")
    path.write_text("".join(lines))
    return path


def load_config() -> dict:
    raw = os.environ.get(ENV_JSON)
    if raw is None and not os.environ.get("AI_QUOTAS_SUBSCRIPTIONS"):
        raw = dotenv_config()
    try:
        data = json.loads(raw if raw is not None else config_path().read_text())
    except FileNotFoundError:
        return {}
    if not isinstance(data, dict) or not isinstance(data.get("providers", {}), dict):
        raise ValueError("subscriptions.json must contain a providers object")
    return data.get("providers", {})


def _number(value: Any, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("subscription values must be finite numbers")
    if value < 0 or (positive and value == 0):
        raise ValueError("subscription values must be nonnegative; allocations must be positive")
    return float(value)


def normalize_plan(plan: Any) -> str:
    return str(plan or "").strip().lower().replace("chatgpt ", "").replace(" ", "_").replace("-", "_")


def resolve(provider: str, plan: str | None, config: dict | None = None) -> dict:
    config = load_config() if config is None else config
    key = normalize_plan(plan)
    result = {"plan": plan or None, "label": plan or "Plan not reported", "monthly_usd": None,
              "regular_allocations": None, "included_resets": 0.0, "source": "unknown"}
    entry = config.get(provider)
    # Bind an override to the reported tier so upgrades/downgrades do not
    # silently inherit a price from a different plan.
    if isinstance(entry, dict) and normalize_plan(entry.get("plan")) == key:
        result.update(
            monthly_usd=_number(entry["monthly_usd"]),
            regular_allocations=_number(entry["regular_allocations"], positive=True),
            included_resets=_number(entry.get("included_resets", 0)),
            label=str(entry.get("label") or plan or "Configured subscription"),
            source="configured",
        )
        return result
    if provider == "claude":
        if "max_20x" in key or key == "max_20x":
            price, label = 200.0, "Claude Max 20x"
        elif "max_5x" in key or key == "max_5x":
            price, label = 100.0, "Claude Max 5x"
        elif key == "pro" or key.startswith("pro+"):
            price, label = 20.0, "Claude Pro"
        else:
            return result
    elif provider == "codex":
        catalog = {"plus": (20.0, "ChatGPT Plus"), "pro_100": (100.0, "ChatGPT Pro $100"),
                   "pro_200": (200.0, "ChatGPT Pro $200"), "pro_5x": (100.0, "ChatGPT Pro 5x"),
                   "pro_20x": (200.0, "ChatGPT Pro 20x")}
        if key not in catalog:  # plain "pro" does not identify the $100/$200 tier
            return result
        price, label = catalog[key]
    else:
        return result
    result.update(monthly_usd=price, label=label, source="detected list price")
    return result


def allocation_value(subscription: dict, hours: float) -> float | None:
    monthly = subscription.get("monthly_usd")
    if monthly is None or hours < 24:
        return None
    regular = subscription.get("regular_allocations") or 720.0 / hours
    return monthly / (regular + subscription.get("included_resets", 0))


def describe(subscription: dict, hours: float) -> str:
    value = allocation_value(subscription, hours)
    if value is None:
        return f"{subscription['label']} · subscription price unknown"
    regular = subscription.get("regular_allocations") or 720.0 / hours
    return (f"{subscription['label']} · ${subscription['monthly_usd']:g}/month ÷ "
            f"({regular:g} regular allocations + {subscription['included_resets']:g} included resets) "
            f"= ${value:.2f} per full allocation · {subscription['source']}")


def configure(provider: str, plan: str, monthly_usd: float, regular_allocations: float, included_resets: float) -> Path:
    if os.environ.get(ENV_JSON) is not None:
        raise ValueError("Subscription settings are supplied by the process environment. Update AI_QUOTAS_SUBSCRIPTIONS_JSON there, or remove it to enable saving from the dashboard.")
    providers = load_config()
    entry = {"plan": plan, "monthly_usd": monthly_usd, "regular_allocations": regular_allocations,
             "included_resets": included_resets}
    resolve(provider, plan, {provider: entry})  # validate before writing
    providers[provider] = entry
    if not os.environ.get("AI_QUOTAS_SUBSCRIPTIONS") and dotenv_config() is not None:
        return save_dotenv(providers)
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps({"providers": providers}, indent=2) + "\n")
    tmp.replace(path)
    return path
