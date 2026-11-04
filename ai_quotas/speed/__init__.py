"""Per-response tokens/sec from local AI chat logs on this Mac."""

from ai_quotas.speed.scan import scan
from ai_quotas.speed.stats import speed_payload, summary

__all__ = ["scan", "speed_payload", "summary"]
