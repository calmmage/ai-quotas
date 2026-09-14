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


# --- auto-detect (multiple versions) -----------------------------------------
# Version A: vendor plan label (Claude max_5x / max_20x is unambiguous;
#            Codex "pro" is not — it does not say $100 vs $200).
# Version B: 5x / 20x token in the plan string (same evidence as A when
#            the vendor reports a multiplier; Plus/Pro without it stays open).
# Version C: estimated weekly token window from local spend (tokens_per_percent
#            × 100). Bands are wide on purpose — Plus vs 5x vs 20x is a
#            ~4–5× jump, not a close call.

_TOKEN_WINDOW_BANDS: dict[str, list[tuple[float, float | None, str, float, str]]] = {
    "claude": [
        (0.0, 40_000_000.0, "pro", 20.0, "Claude Pro"),
        (40_000_000.0, 160_000_000.0, "max_5x", 100.0, "Claude Max 5x"),
        (160_000_000.0, None, "max_20x", 200.0, "Claude Max 20x"),
    ],
    "codex": [
        (0.0, 40_000_000.0, "plus", 20.0, "ChatGPT Plus"),
        (40_000_000.0, 160_000_000.0, "pro_5x", 100.0, "ChatGPT Pro 5x"),
        (160_000_000.0, None, "pro_20x", 200.0, "ChatGPT Pro 20x"),
    ],
}

_MULTIPLIER_PRICE = {
    ("claude", 1): (20.0, "Claude Pro", "pro"),
    ("claude", 5): (100.0, "Claude Max 5x", "max_5x"),
    ("claude", 20): (200.0, "Claude Max 20x", "max_20x"),
    ("codex", 1): (20.0, "ChatGPT Plus", "plus"),
    ("codex", 5): (100.0, "ChatGPT Pro 5x", "pro_5x"),
    ("codex", 20): (200.0, "ChatGPT Pro 20x", "pro_20x"),
}


def _plan_multiplier(provider: str, plan: str | None) -> int | None:
    key = normalize_plan(plan)
    if "20x" in key:
        return 20
    if "5x" in key:
        return 5
    if provider == "claude" and (key == "pro" or key.startswith("pro+")):
        return 1
    if provider == "codex" and key == "plus":
        return 1
    return None


def _band_for_tokens(provider: str, window_tokens: float) -> tuple[str, float, str] | None:
    for lo, hi, plan_id, usd, label in _TOKEN_WINDOW_BANDS.get(provider, []):
        if window_tokens >= lo and (hi is None or window_tokens < hi):
            return plan_id, usd, label
    return None


def detect_versions(
    provider: str,
    plan: str | None,
    *,
    window_tokens: float | None = None,
) -> list[dict[str, Any]]:
    """Return ranked detection attempts. First confident priced row is the pick."""
    labeled = resolve(provider, plan, {})
    versions: list[dict[str, Any]] = [
        {
            "id": "plan-label",
            "confident": labeled.get("monthly_usd") is not None,
            "monthly_usd": labeled.get("monthly_usd"),
            "label": labeled.get("label"),
            "plan": labeled.get("plan") or plan,
            "source": labeled.get("source"),
            "note": "Vendor plan string. Codex 'pro' does not distinguish $100 vs $200.",
        }
    ]
    multiplier = _plan_multiplier(provider, plan)
    priced = _MULTIPLIER_PRICE.get((provider, multiplier)) if multiplier else None
    versions.append(
        {
            "id": "limit-multiplier",
            "confident": priced is not None,
            "monthly_usd": None if priced is None else priced[0],
            "label": None if priced is None else priced[1],
            "plan": None if priced is None else priced[2],
            "multiplier": multiplier,
            "source": "5x/20x in plan label" if priced else "no 5x/20x in plan label",
            "note": "Same jump Petr called obvious: 5× vs 20× vs Plus, from the plan string.",
        }
    )
    band = (
        _band_for_tokens(provider, float(window_tokens))
        if window_tokens is not None and window_tokens > 0
        else None
    )
    versions.append(
        {
            "id": "token-window",
            "confident": band is not None and float(window_tokens or 0) >= 5_000_000,
            "monthly_usd": None if band is None else band[1],
            "label": None if band is None else band[2],
            "plan": None if band is None else band[0],
            "window_tokens": window_tokens,
            "source": "spend calibration (tokens per 1% × 100)",
            "note": "Wide Plus / 5x / 20x bands from local spend in the current reset period.",
        }
    )
    return versions


def pick_detection(versions: list[dict[str, Any]]) -> dict[str, Any] | None:
    for row in versions:
        if row.get("confident") and row.get("monthly_usd") is not None:
            return row
    return None
