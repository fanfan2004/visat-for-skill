"""Durable append-only JSONL storage for telemetry events."""

from __future__ import annotations

import json
import os
import re
import threading
from collections.abc import Iterator
from pathlib import Path

from .models import EventRecord

_SAFE_TRIAL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class EventLogError(RuntimeError):
    pass


class JsonlEventLog:
    def __init__(self, path: str | Path, *, expected_trial_id: str | None = None) -> None:
        self.path = Path(path)
        self.expected_trial_id = expected_trial_id
        self._lock = threading.RLock()
        self._last_steps: dict[str, int] | None = None

    @classmethod
    def for_trial(cls, project_root: str | Path, trial_id: str) -> JsonlEventLog:
        if not _SAFE_TRIAL_ID.fullmatch(trial_id):
            raise ValueError(f"unsafe trial_id: {trial_id!r}")
        project = Path(project_root).resolve()
        raw_root = project / "data" / "raw"
        path = raw_root / f"{trial_id}.jsonl"
        try:
            path.resolve().relative_to(raw_root.resolve())
        except ValueError as exc:
            raise ValueError("trial log path escapes data/raw") from exc
        return cls(path, expected_trial_id=trial_id)

    def append(self, event: EventRecord) -> None:
        if self.expected_trial_id is not None and event.trial_id != self.expected_trial_id:
            raise EventLogError(
                f"event trial_id {event.trial_id!r} does not match log "
                f"{self.expected_trial_id!r}"
            )
        with self._lock:
            last_steps = self._load_last_steps()
            previous = last_steps.get(event.trial_id)
            if previous is not None and event.step <= previous:
                raise EventLogError(
                    f"step must increase for trial {event.trial_id!r}: "
                    f"received {event.step} after {previous}"
                )
            self.path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(
                event.to_dict(), ensure_ascii=False, separators=(",", ":"), sort_keys=True
            )
            with self.path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(line)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            last_steps[event.trial_id] = event.step

    def read_events(self) -> Iterator[EventRecord]:
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, start=1):
                if not line.strip():
                    continue
                try:
                    raw = json.loads(line)
                    yield EventRecord.from_dict(raw)
                except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                    raise EventLogError(
                        f"invalid event at {self.path}:{line_number}: {exc}"
                    ) from exc

    def _load_last_steps(self) -> dict[str, int]:
        if self._last_steps is not None:
            return self._last_steps
        steps: dict[str, int] = {}
        for event in self.read_events():
            previous = steps.get(event.trial_id)
            if previous is not None and event.step <= previous:
                raise EventLogError(
                    f"non-increasing stored step for {event.trial_id!r}: {event.step}"
                )
            steps[event.trial_id] = event.step
        self._last_steps = steps
        return steps
