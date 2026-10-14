"""Launch both Mac endpoint controllers with shared resource quotas and resumable names."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from sj4_extension_tasks import CONDITIONS

REPO = Path(__file__).resolve().parents[2]


def batch_name(task: str, stamp: str, replicates: int) -> str:
    return f"formal-{task}-sj4ext-six-x{replicates}-glm53-{stamp}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stamp", required=True, help="stable batch label, e.g. 20261014-2130")
    parser.add_argument("--workers", type=int, default=10, help="episodes per endpoint, not replicates")
    parser.add_argument("--replicates", type=int, default=5)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args()
    if not args.stamp or any(ch not in "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ-_"
                             for ch in args.stamp):
        parser.error("stamp must contain only letters, digits, dash or underscore")
    if not 1 <= args.workers <= 10 or not 1 <= args.replicates <= 10:
        parser.error("workers and replicates must be between 1 and 10")
    assignments = (("task-014", "new-provider"), ("task-015", "new-provider-2"))
    if args.status:
        for task, _ in assignments:
            root = REPO / "data/raw" / batch_name(task, args.stamp, args.replicates)
            rows = [json.loads(p.read_text(encoding="utf-8"))
                    for p in sorted(root.glob("*/replicate-*/result.json"))]
            print(root.name, f"completed={len(rows)}/{len(CONDITIONS) * args.replicates}")
            for condition in CONDITIONS:
                values = [r for r in rows if r["condition"] == condition]
                valid = [r for r in values if not r["status"].startswith("quarantined_")]
                print(f"  {condition}: valid={len(valid)} quarantined={len(values)-len(valid)} "
                      f"first={sum(r['vista_metrics']['first_turn_pass'] for r in valid)} "
                      f"final={sum(r['final_verifier_pass'] for r in valid)}")
        return 0
    if sys.platform != "darwin":
        parser.error("this launcher is for macOS; use local unit tests on Windows")
    python = Path(os.environ.get("VISTA_VERIFIER_PYTHON", REPO / ".venv/bin/python")).expanduser().resolve()
    if not python.is_file():
        parser.error(f"missing verifier/agent Python: {python}")
    def stop_requested(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop_requested)
    signal.signal(signal.SIGHUP, stop_requested)
    logs = REPO / "data/derived" / f"sj4ext-logs-{args.stamp}"
    logs.mkdir(parents=True, exist_ok=True)
    try:
        hardware = {name: int(subprocess.check_output(["sysctl", "-n", name], text=True).strip())
                    for name in ("hw.memsize", "hw.ncpu")}
    except (OSError, subprocess.CalledProcessError, ValueError):
        hardware = {}
    (logs / f"host-resources-{time.time_ns()}.json").write_text(json.dumps({
        **hardware, "workers_per_endpoint": args.workers, "replicates": args.replicates,
        "solver_workers": 2, "verifier_workers": 1, "numeric_threads": 1,
    }, indent=2) + "\n", encoding="utf-8")
    if hardware.get("hw.memsize", 0) <= 16 * 1024**3 and args.workers > 4:
        print(f"Memory is 16GB or less (or unknown): {args.workers} workers per endpoint may cause swapping. "
              "Watch Memory Pressure; Ctrl+C and resume with --workers 3 if necessary.", flush=True)
    processes = []
    streams = []
    interrupted = False
    try:
        for task, provider in assignments:
            env = os.environ.copy()
            env["VISTA_RUN_PREFIX"] = batch_name(task, args.stamp, args.replicates)
            env["VISTA_RESOURCE_GUARD_ROOT"] = str(
                Path.home() / "Library/Caches/VistaExperiments" / ".sj4-extension-guard-v1"
            )
            env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + str(REPO / "experiments/formal")
            env["PYTHONUNBUFFERED"] = "1"
            env["VISTA_VERIFIER_PYTHON"] = str(python)
            command = [str(python), str(REPO / "experiments/formal/run_sj4_zcode_group.py"),
                       "--task", task, "--provider", provider,
                       "--conditions", *CONDITIONS,
                       "--replicates", str(args.replicates), "--workers", str(args.workers),
                       "--verifier-workers", "1", "--solver-workers", "2", "--numeric-threads", "1"]
            if args.dry_run:
                command.append("--dry-run")
            log = logs / f"{task}-{'preflight' if args.dry_run else 'run'}.log"
            stream = log.open("a", encoding="utf-8")
            streams.append(stream)
            proc = subprocess.Popen(command, cwd=REPO, env=env, stdout=stream,
                                    stderr=subprocess.STDOUT, start_new_session=True)
            processes.append((task, proc))
            print(f"{task}: pid={proc.pid}, workers={args.workers}, log={log}", flush=True)
        while any(proc.poll() is None for _, proc in processes):
            time.sleep(1)
        for task, proc in processes:
            print(f"{task}: exit={proc.returncode}", flush=True)
        return 0 if all(proc.returncode == 0 for _, proc in processes) else 1
    except KeyboardInterrupt:
        interrupted = True
        print("Stopping both endpoint process groups; completed trial evidence is preserved.", flush=True)
        return 130
    finally:
        # Includes model/tool child processes, avoiding orphaned solvers after Ctrl+C.
        for _, proc in processes:
            if interrupted or proc.poll() is None:
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
        for _, proc in processes:
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
        for stream in streams:
            stream.close()


if __name__ == "__main__":
    raise SystemExit(main())
