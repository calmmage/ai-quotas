"""Petr's spawn tiers. Pick stays backward compatible; this map is opt-in.

Smart builds: Codex Sol/Astra, Gemini Argon, Claude Opus.
Fable NEVER builds. Fable only plans.
Cheap context: Muse, Gemini Flash, Gemini Pro.
Grok sits in the middle.

Harness ids that share a provider (gemini-flash, gemini-argon, claude-opus,
claude-fable) judge only the windows listed here. A green 5h window still
cannot hide a STOP week: 5h windows are dropped before this allow-list.
"""

from __future__ import annotations

from typing import Any

# harness → (provider, windows or None). None = every non-5h window.
HARNESS_SPEC: dict[str, tuple[str, tuple[str, ...] | None]] = {
    "grok": ("grok", None),
    "claude": ("claude", None),
    "codex": ("codex", None),
    "muse": ("muse", None),
    "agy": ("agy", None),
    "gemini-flash": ("agy", ("week_gemini_flash",)),
    "gemini-argon": ("agy", ("week_gemini_pro", "week_gemini_argon")),
    "gemini-pro": ("agy", ("week_gemini_pro",)),
    "claude-opus": ("claude", ("week",)),
    "claude-fable": ("claude", ("week_fable",)),
}

# role build = may build; plan = Fable only; context = cheap gathering; either = middle.
TIERS: dict[str, tuple[dict[str, str], ...]] = {
    "smart": (
        {"harness": "codex", "model": "sol/astra", "role": "build", "label": "Codex Sol/Astra"},
        {"harness": "gemini-argon", "model": "argon", "role": "build", "label": "Gemini Argon"},
        {"harness": "claude-opus", "model": "opus", "role": "build", "label": "Claude Opus"},
        {"harness": "claude-fable", "model": "fable", "role": "plan", "label": "Claude Fable"},
    ),
    "cheap": (
        {"harness": "muse", "model": "muse", "role": "context", "label": "Muse"},
        {"harness": "gemini-flash", "model": "flash", "role": "context", "label": "Gemini Flash"},
        {"harness": "gemini-pro", "model": "pro", "role": "context", "label": "Gemini Pro"},
    ),
    "middle": (
        {"harness": "grok", "model": "grok", "role": "either", "label": "Grok"},
    ),
}

# Names `pick --candidates` grew. The default list stays grok,claude,codex.
EXTRA_PICK_HARNESSES = ("muse", "agy", "gemini-flash", "gemini-argon")


def candidates_for(tier: str, *, role: str | None = None) -> tuple[str, ...]:
    """Harness ids for a tier. Smart omits Fable unless role is plan."""
    rows = TIERS[tier]
    if role is None and tier == "smart":
        rows = tuple(row for row in rows if row["role"] == "build")
    elif role is not None:
        rows = tuple(row for row in rows if row["role"] == role)
    out: list[str] = []
    for row in rows:
        if row["harness"] not in out:
            out.append(row["harness"])
    return tuple(out)


def describe_harness(harness: str) -> dict[str, Any]:
    for rows in TIERS.values():
        for row in rows:
            if row["harness"] == harness:
                return dict(row)
    provider, _windows = HARNESS_SPEC.get(harness, (harness, None))
    return {"harness": harness, "model": harness, "role": "build", "label": harness, "provider": provider}
