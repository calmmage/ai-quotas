"""Vendor quota adapters.

Each module exposes ``snapshot(ts: str) -> list[dict]`` and never raises.
Failure → one row with status in {unavailable, error} and used_percent=None.
Never fabricate used_percent: 0 on failure.
"""
