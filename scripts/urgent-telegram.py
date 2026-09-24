#!/usr/bin/env python3
"""DM a quota alert from the primary account to @petrlavrovurgent.

Reads the message on stdin. This only works on the Mac that holds the
primary Telethon session. The cloud plot mirror must not run it.

Exit 0 after Telegram accepts the message. Any failure exits non-zero so
the sampler can retry on the next run.
"""

from __future__ import annotations

import asyncio
import sys

TARGET = "petrlavrovurgent"


async def _send(text: str) -> None:
    from calmlib.telegram.telethon_client import get_telethon_client_context

    async with get_telethon_client_context("primary") as client:
        await client.send_message(TARGET, text)


def main() -> int:
    text = sys.stdin.read().strip()
    if not text:
        print("urgent-telegram: empty message", file=sys.stderr)
        return 1
    try:
        asyncio.run(_send(text))
    except Exception as exc:  # noqa: BLE001 — the sampler retries on the next sample
        print(f"urgent-telegram: {exc}", file=sys.stderr)
        return 1
    print("urgent-telegram: sent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
