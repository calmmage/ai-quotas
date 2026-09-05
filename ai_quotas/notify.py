"""Telegram + Healthchecks delivery. Stdlib only. Fail-open: never raise into callers."""

from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

ENV_TG_TOKEN = "AI_QUOTAS_TELEGRAM_BOT_TOKEN"
ENV_TG_CHAT = "AI_QUOTAS_TELEGRAM_CHAT_ID"
ENV_TG_TOKEN_FALLBACK = "CALMMAGE_SERVICE_BOT_TOKEN_PROD"
ENV_TG_CHAT_FALLBACK = "CALMMAGE_SERVICE_BOT_CHAT_ID"
ENV_TG_CHAT_FALLBACK_2 = "CALMMAGE_TELEGRAM_MY_CHAT_ID"

ENV_HC_SAMPLE_URL = "AI_QUOTAS_HC_SAMPLE_URL"
ENV_HC_DASH_URL = "AI_QUOTAS_HC_DASH_URL"
ENV_HC_PING_KEY = "CALMMAGE_HEALTHCHECKS_PING_KEY"
ENV_HC_BASE = "CALMMAGE_HEALTHCHECKS_BASE_URL"
ENV_HC_SAMPLE_SLUG = "AI_QUOTAS_SAMPLE_HC_SLUG"
ENV_HC_DASH_SLUG = "AI_QUOTAS_DASH_HC_SLUG"
ENV_HC_INTERVAL = "AI_QUOTAS_HC_INTERVAL"

DEFAULT_HC_BASE = "https://healthchecks.calmmage.com"
DEFAULT_SAMPLE_SLUG = "ai-quotas-sample"
DEFAULT_DASH_SLUG = "ai-quotas-dash"
DEFAULT_HC_INTERVAL = 300.0

_DOTENV = Path.home() / ".env"
_CALMMAGE_ROOTS = (
    Path.home() / "work" / "calmmage",
    Path.home() / "calmmage",
)


def _read_dotenv_enabled() -> bool:
    raw = (os.environ.get("AI_QUOTAS_READ_DOTENV") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _dotenv_get(name: str) -> str:
    if not _read_dotenv_enabled():
        return ""
    if not _DOTENV.is_file():
        return ""
    try:
        text = _DOTENV.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    for line in text.splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, _, value = raw.partition("=")
        if key.strip() == name:
            return value.strip().strip("'").strip('"')
    return ""


def _calmmage_key(name: str) -> str:
    """Owner-machine fallback: calmlib encrypted env. Off unless dotenv flag is on."""
    if not _read_dotenv_enabled():
        return ""
    for root in _CALMMAGE_ROOTS:
        py = root / ".venv" / "bin" / "python"
        if not py.is_file():
            continue
        try:
            proc = subprocess.run(
                [
                    str(py),
                    "-c",
                    (
                        "from calmlib.utils import find_calmmage_env_key as f\n"
                        f"print(f({name!r}) or '')"
                    ),
                ],
                capture_output=True,
                text=True,
                timeout=20,
                cwd=str(root),
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        val = (proc.stdout or "").strip().splitlines()
        if val and val[-1]:
            return val[-1]
    return ""


def env_or_dotenv(*names: str) -> str:
    for name in names:
        raw = os.environ.get(name) or _dotenv_get(name)
        if raw and raw.strip():
            return raw.strip()
    return ""


def resolve_telegram() -> tuple[str, str]:
    token = env_or_dotenv(ENV_TG_TOKEN, ENV_TG_TOKEN_FALLBACK)
    chat = env_or_dotenv(ENV_TG_CHAT, ENV_TG_CHAT_FALLBACK, ENV_TG_CHAT_FALLBACK_2)
    if not token:
        token = _calmmage_key(ENV_TG_TOKEN) or _calmmage_key(ENV_TG_TOKEN_FALLBACK)
    if not chat:
        chat = (
            _calmmage_key(ENV_TG_CHAT)
            or _calmmage_key(ENV_TG_CHAT_FALLBACK)
            or _calmmage_key(ENV_TG_CHAT_FALLBACK_2)
        )
    return token, chat


def send_telegram(text: str, *, token: str | None = None, chat: str | None = None) -> str:
    """Send a plain-text Telegram message. Returns 'sent' | 'skip' | 'error:…'."""
    tok = token if token is not None else resolve_telegram()[0]
    cid = chat if chat is not None else resolve_telegram()[1]
    if not tok or not cid:
        return "skip"
    body = urllib.parse.urlencode(
        {
            "chat_id": cid,
            "text": text[:4000],
            "disable_web_page_preview": "true",
        }
    ).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{tok}/sendMessage",
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return f"error:{exc}"
    try:
        ok = bool(json.loads(raw).get("ok"))
    except json.JSONDecodeError:
        return "error:bad-json"
    return "sent" if ok else "error:not-ok"


def _compose_hc_url(role: str) -> str:
    if role == "dash":
        explicit = env_or_dotenv(ENV_HC_DASH_URL)
        slug = env_or_dotenv(ENV_HC_DASH_SLUG)
    else:
        explicit = env_or_dotenv(ENV_HC_SAMPLE_URL)
        slug = env_or_dotenv(ENV_HC_SAMPLE_SLUG)
    if explicit:
        return explicit.rstrip("/")
    key = env_or_dotenv(ENV_HC_PING_KEY)
    if not key or not slug:
        return ""
    base = (env_or_dotenv(ENV_HC_BASE) or DEFAULT_HC_BASE).rstrip("/")
    return f"{base}/ping/{key}/{slug}"


def ping_healthchecks(url: str, *, suffix: str = "") -> str:
    """GET a Healthchecks ping URL. Returns 'ok' | 'skip' | 'error:…'."""
    if not url:
        return "skip"
    target = url.rstrip("/") + suffix
    req = urllib.request.Request(target, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
        return "ok"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return f"error:{exc}"


def ping_role(role: str, *, suffix: str = "") -> str:
    """Ping the sample or dash check. role is 'sample' | 'dash'."""
    return ping_healthchecks(_compose_hc_url(role), suffix=suffix)


def heartbeat_due(last: float, now: float, every: float) -> bool:
    if every <= 0:
        return False
    return (now - last) >= every


def hc_interval() -> float:
    raw = env_or_dotenv(ENV_HC_INTERVAL)
    if not raw:
        return DEFAULT_HC_INTERVAL
    try:
        val = float(raw)
    except ValueError:
        return DEFAULT_HC_INTERVAL
    return val if val > 0 else DEFAULT_HC_INTERVAL


def doctor_notify_lines() -> list[str]:
    keys = (
        ENV_TG_TOKEN,
        ENV_TG_CHAT,
        ENV_HC_SAMPLE_URL,
        ENV_HC_DASH_URL,
        ENV_HC_PING_KEY,
        ENV_HC_BASE,
        ENV_HC_SAMPLE_SLUG,
        ENV_HC_DASH_SLUG,
        ENV_HC_INTERVAL,
        "AI_QUOTAS_READ_DOTENV",
    )
    lines = []
    for key in keys:
        raw = os.environ.get(key)
        lines.append(f"  {key}={'set' if raw else '(unset)'}")
    return lines


def notify_status() -> dict[str, Any]:
    token, chat = resolve_telegram()
    return {
        "telegram": bool(token and chat),
        "hc_sample": bool(_compose_hc_url("sample")),
        "hc_dash": bool(_compose_hc_url("dash")),
    }
