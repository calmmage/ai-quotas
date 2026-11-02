"""A small PNG usage plot for Telegram alerts and the morning report. Stdlib only.

Petr, 3 Nov 2026: «Attach usage plot of corresponding to each notification.
Just for fun.» The dashboard plots are HTML; the package core has no image
dependency, so this draws remaining % over the last week directly:
a filled line per window, the dashed pace guide, day ticks, 0/50/100.
"""
from __future__ import annotations

import math
import struct
import zlib
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any, Iterable

from ai_quotas import core
from ai_quotas.accounts import normalize_account

# Same colours as the dashboard series (plots/prep.py COLORS).
COLORS = {
    ("claude", "week"): "#C49A6C",
    ("claude", "week_fable"): "#E67E22",
    ("codex", "week"): "#5B9BD5",
    ("grok", "week"): "#7CB87C",
    ("grok", "month"): "#2E7D4F",
    ("agy", "week_gemini_pro"): "#9B6FC0",
    ("agy", "week_opus"): "#2E9C93",
    ("muse", "week"): "#1877F2",
}
# The windows one app's plot shows; the first is the main one (pace guide).
PLOT_WINDOWS = {
    "claude": ("week", "week_fable"),
    "codex": ("week",),
    "grok": ("week",),
    "agy": ("week_gemini_pro", "week_opus"),
    "muse": ("week",),
}
WIDTH, HEIGHT = 800, 340
BG = (22, 24, 29)
GRID = (52, 56, 64)
TEXT = (138, 143, 152)
PACE = (110, 114, 122)
SPAN = timedelta(days=7)

_DIGITS = {
    "0": "111101101101111", "1": "010110010010111", "2": "111001111100111",
    "3": "111001111001111", "4": "101101111001001", "5": "111100111001111",
    "6": "111100111101111", "7": "111001001001001", "8": "111101111101111",
    "9": "111101111001111", "%": "101001010100101",
    "A": "010101111101101", "B": "110101110101110", "C": "011100100100011",
    "D": "110101101101110", "E": "111100110100111", "F": "111100110100100",
    "G": "011100101101011", "H": "101101111101101", "I": "111010010010111",
    "J": "001001001101010", "K": "101101110101101", "L": "100100100100111",
    "M": "101111111101101", "N": "110101101101101", "O": "010101101101010",
    "P": "110101110100100", "Q": "010101101110011", "R": "110101110101101",
    "S": "011100010001110", "T": "111010010010010", "U": "101101101101111",
    "V": "101101101101010", "W": "101101111111101", "X": "101101010101101",
    "Y": "101101010010010", "Z": "111001010100111", ".": "000000000000010",
    "-": "000000111000000", "_": "000000000000111", "+": "000010111010000",
}


def _rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


class Canvas:
    def __init__(self, width: int, height: int, bg: tuple[int, int, int]):
        self.w, self.h = width, height
        self.px = bytearray(bytes(bg) * (width * height))

    def blend(self, x: int, y: int, color: tuple[int, int, int], alpha: float) -> None:
        if not (0 <= x < self.w and 0 <= y < self.h) or alpha <= 0:
            return
        i = (y * self.w + x) * 3
        a = min(1.0, alpha)
        p = self.px
        p[i] = int(p[i] + (color[0] - p[i]) * a)
        p[i + 1] = int(p[i + 1] + (color[1] - p[i + 1]) * a)
        p[i + 2] = int(p[i + 2] + (color[2] - p[i + 2]) * a)

    def hline(self, x0: int, x1: int, y: int, color, alpha: float = 1.0, dash: int = 0) -> None:
        for x in range(max(0, x0), min(self.w, x1 + 1)):
            if not dash or (x // dash) % 2 == 0:
                self.blend(x, y, color, alpha)

    def vline(self, x: int, y0: int, y1: int, color, alpha: float = 1.0, dash: int = 0) -> None:
        for y in range(max(0, y0), min(self.h, y1 + 1)):
            if not dash or (y // dash) % 2 == 0:
                self.blend(x, y, color, alpha)

    def line(self, x0: float, y0: float, x1: float, y1: float, color, width: float = 2.0,
             alpha: float = 1.0, dash: float = 0.0) -> None:
        """Anti-aliased thick segment by distance-to-segment coverage."""
        half = width / 2.0
        dx, dy = x1 - x0, y1 - y0
        length2 = dx * dx + dy * dy
        length = math.sqrt(length2)
        for y in range(max(0, int(min(y0, y1) - half - 1)), min(self.h, int(max(y0, y1) + half + 2))):
            for x in range(max(0, int(min(x0, x1) - half - 1)), min(self.w, int(max(x0, x1) + half + 2))):
                t = 0.0 if length2 == 0 else max(0.0, min(1.0, ((x - x0) * dx + (y - y0) * dy) / length2))
                if dash and int(t * length / dash) % 2:
                    continue
                dist = math.hypot(x - (x0 + t * dx), y - (y0 + t * dy))
                cover = half + 0.5 - dist
                if cover > 0:
                    self.blend(x, y, color, alpha * min(1.0, cover))

    def text(self, x: int, y: int, value: str, color, scale: int = 2) -> None:
        for glyph in value.upper():
            bits = _DIGITS.get(glyph)
            if bits:
                for k, bit in enumerate(bits):
                    if bit == "1":
                        gx, gy = k % 3, k // 3
                        for sy in range(scale):
                            for sx in range(scale):
                                self.blend(x + gx * scale + sx, y + gy * scale + sy, color, 1.0)
            x += 4 * scale

    def png(self) -> bytes:
        raw = b"".join(b"\x00" + bytes(self.px[r * self.w * 3:(r + 1) * self.w * 3]) for r in range(self.h))

        def chunk(tag: bytes, data: bytes) -> bytes:
            return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

        header = struct.pack(">IIBBBBB", self.w, self.h, 8, 2, 0, 0, 0)
        return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
                + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def _ts(row: dict[str, Any]) -> datetime | None:
    value = core.parse_ts(row.get("observed_at") or row.get("ts"))
    if value is None:
        return None
    return value.replace(tzinfo=value.tzinfo or timezone.utc).astimezone(timezone.utc)


def series_for(samples: Iterable[dict[str, Any]], provider: str, account: str | None,
               *, start: datetime, end: datetime) -> list[dict[str, Any]]:
    """Remaining % points per plotted window of one app and login."""
    want = PLOT_WINDOWS.get(provider, ("week",))
    login = normalize_account(account)
    points: dict[str, list[tuple[datetime, float, datetime | None]]] = {w: [] for w in want}
    for row in samples:
        window = str(row.get("window") or "")
        if row.get("provider") != provider or window not in points or row.get("status") != "ok":
            continue
        if login and normalize_account(row.get("account")) not in {login, None}:
            continue
        used = row.get("used_percent")
        at = _ts(row)
        if not isinstance(used, (int, float)) or not math.isfinite(used) or at is None:
            continue
        if not start - timedelta(hours=6) <= at <= end:
            continue
        deadline = core.parse_ts(row.get("resets_at")) if isinstance(row.get("resets_at"), str) else None
        points[window].append((at, max(0.0, min(100.0, 100.0 - float(used))), deadline))
    out = []
    for window in want:
        pts = sorted(points[window], key=lambda p: p[0])
        if pts:
            out.append({"window": window, "color": COLORS.get((provider, window), "#8B8F98"), "points": pts})
    return out


def render(series: list[dict[str, Any]], *, now: datetime, tz: tzinfo | None = None,
           width: int = WIDTH, height: int = HEIGHT, title: str = "") -> bytes | None:
    """PNG bytes, or None when there is nothing to draw."""
    if not series:
        return None
    now = now.astimezone(timezone.utc)
    tz = tz or timezone.utc
    main = series[0]["points"]
    deadline = next((d for _, _, d in reversed(main) if d is not None), None)
    if deadline is not None:
        deadline = deadline.replace(tzinfo=deadline.tzinfo or timezone.utc).astimezone(timezone.utc)
    end = deadline if deadline is not None and now < deadline <= now + timedelta(days=2) else now
    end = max(end, now) + timedelta(hours=2)
    start = end - SPAN - timedelta(hours=2)
    left, right, top, bottom = 52, width - 16, 14, height - 34
    c = Canvas(width, height, BG)

    def x_of(t: datetime) -> float:
        return left + (t - start).total_seconds() / (end - start).total_seconds() * (right - left)

    def y_of(pct: float) -> float:
        return bottom - pct / 100.0 * (bottom - top)

    for pct in (0, 50, 100):
        y = int(round(y_of(pct)))
        c.hline(left, right, y, GRID, 1.0 if pct == 0 else 0.7, dash=0 if pct == 0 else 4)
        label = str(pct)
        c.text(left - 10 - len(label) * 8, y - 5, label, TEXT)
    day = start.astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    while day.astimezone(timezone.utc) < end:
        x = int(round(x_of(day.astimezone(timezone.utc))))
        c.vline(x, top, bottom, GRID, 0.5, dash=3)
        label = str(day.day)
        c.text(x - len(label) * 4 + 1, bottom + 10, label, TEXT)
        day += timedelta(days=1)
    if deadline is not None:
        window_start = deadline - (timedelta(days=30) if series[0]["window"].startswith("month") else SPAN)
        a, b = max(window_start, start), min(deadline, end)
        if a < b:
            frac = lambda t: 100.0 * (deadline - t).total_seconds() / (deadline - window_start).total_seconds()
            c.line(x_of(a), y_of(frac(a)), x_of(b), y_of(frac(b)), PACE, width=1.6, dash=7)
        if start < deadline < end:
            c.vline(int(round(x_of(deadline))), top, bottom, PACE, 0.9, dash=5)
    now_x = int(round(x_of(now)))
    c.vline(now_x, top, bottom, TEXT, 0.35)
    for index, s in enumerate(series):
        color = _rgb(s["color"])
        pts = [(x_of(t), y_of(r)) for t, r, _ in s["points"] if t >= start - timedelta(hours=6)]
        if not pts:
            continue
        clipped = [(max(left, min(right, x)), y) for x, y in pts]
        if index == 0:
            # Light fill under the main window's step line, one pass per column.
            tops: dict[int, int] = {}
            for k, (x, y) in enumerate(clipped):
                nxt = clipped[k + 1][0] if k + 1 < len(clipped) else min(float(now_x), right)
                for col in range(int(round(x)), int(round(nxt))):
                    tops[col] = int(round(y))
            for col, row0 in tops.items():
                for row in range(row0, bottom):
                    c.blend(col, row, color, 0.16)
        for k in range(len(clipped)):
            x, y = clipped[k]
            nx = clipped[k + 1][0] if k + 1 < len(clipped) else min(float(now_x), right)
            c.line(x, y, nx, y, color, width=2.4)
            if k + 1 < len(clipped):
                c.line(nx, y, nx, clipped[k + 1][1], color, width=2.4)
        lx, ly = clipped[-1]
        c.line(min(float(now_x), right), ly, min(float(now_x), right), ly, color, width=8.0)
    if title:
        # The album shows one caption; each photo names its app and login.
        c.text(left + 10, top + 8, title, _rgb(series[0]["color"]), scale=2)
    return c.png()


def plot_png(samples: Iterable[dict[str, Any]], provider: str, account: str | None, *,
             now: datetime, tz: tzinfo | None = None) -> bytes | None:
    """The usage plot for one app and login; None on no data or any failure."""
    try:
        moment = now.replace(tzinfo=now.tzinfo or timezone.utc).astimezone(timezone.utc)
        rows = list(samples)
        series = series_for(rows, provider, account, start=moment - SPAN - timedelta(days=1),
                            end=moment)
        from ai_quotas.alert_text import app_name, login_tag

        title = f"{app_name(provider)}  {login_tag(account)}".strip()
        return render(series, now=moment, tz=tz, title=title)
    except Exception:  # noqa: BLE001 — a plot failure must never stop the text alert
        return None
