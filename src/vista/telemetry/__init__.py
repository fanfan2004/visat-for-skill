"""Append-only JSONL telemetry for replayable experiment trajectories."""

from .jsonl import EventLogError, JsonlEventLog
from .models import EventRecord, EventType

__all__ = ["EventLogError", "EventRecord", "EventType", "JsonlEventLog"]
