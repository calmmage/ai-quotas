"""Shared fixtures for offline ai-quotas tests. No network, no real vendor dirs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture
def multi_samples(fixtures_dir: Path) -> list[dict]:
    path = fixtures_dir / "multi.jsonl"
    rows = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


@pytest.fixture
def multi_path(fixtures_dir: Path) -> Path:
    return fixtures_dir / "multi.jsonl"


@pytest.fixture
def tmp_samples(tmp_path: Path) -> Path:
    p = tmp_path / "samples.jsonl"
    p.touch()
    return p
