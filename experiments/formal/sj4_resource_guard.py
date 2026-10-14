"""Host-wide file-lock slots; no task knowledge or solver formulation lives here."""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from pathlib import Path


def _lock(stream) -> bool:
    if os.name == "nt":
        import msvcrt

        stream.seek(0)
        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
    else:
        import fcntl

        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
    return True


def _unlock(stream) -> None:
    if os.name == "nt":
        import msvcrt

        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


@contextmanager
def slots(root: Path, kind: str, count: int):
    """Acquire one slot across threads and processes; release on process exit too."""
    if count < 1:
        raise ValueError("slot count must be positive")
    root.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    while True:
        for index in range(count):
            stream = (root / f"{kind}-{index}.lock").open("a+b")
            if stream.tell() == 0:
                stream.write(b"0")
                stream.flush()
            if _lock(stream):
                try:
                    yield time.monotonic() - started
                finally:
                    _unlock(stream)
                    stream.close()
                return
            stream.close()
        time.sleep(0.1)


def configure(root: Path, *, verifier_workers: int, solver_workers: int,
              numeric_threads: int) -> None:
    """Reject mismatched host quotas between the two endpoint controllers."""
    values = dict(verifier_workers=verifier_workers, solver_workers=solver_workers,
                  numeric_threads=numeric_threads)
    with slots(root, "configuration", 1):
        path = root / "limits.json"
        if path.exists() and json.loads(path.read_text(encoding="utf-8")) != values:
            raise ValueError(f"resource limits differ from {path}; use a new shared guard directory")
        path.write_text(json.dumps(values, indent=2) + "\n", encoding="utf-8")


def log_solver_event(*, wait_seconds: float, elapsed_seconds: float) -> None:
    path = os.environ.get("VISTA_SOLVER_LOG")
    if path:
        with Path(path).open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"pid": os.getpid(), "wait_seconds": wait_seconds,
                                     "elapsed_seconds": elapsed_seconds}) + "\n")


def wrap_milp(original):
    def guarded_milp(c, *, integrality=None, bounds=None, constraints=None, options=None):
        import warnings

        limits = dict(options or {})
        limits["threads"] = int(os.environ["VISTA_NUMERIC_THREADS"])
        limits["parallel"] = False
        root = Path(os.environ["VISTA_RESOURCE_GUARD_ROOT"])
        with slots(root, "solver", int(os.environ["VISTA_SOLVER_WORKERS"])) as waited:
            started = time.monotonic()
            try:
                with warnings.catch_warnings():
                    # SciPy forwards these HiGHS options but warns about their names.
                    warnings.filterwarnings("ignore", message="Unrecognized options detected.*",
                                            category=RuntimeWarning)
                    return original(c, integrality=integrality, bounds=bounds,
                                    constraints=constraints, options=limits)
            finally:
                log_solver_event(wait_seconds=waited,
                                 elapsed_seconds=time.monotonic() - started)
    return guarded_milp
