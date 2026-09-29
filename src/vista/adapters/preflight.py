"""Fail-fast pilot preflight for the six real SkillsBench source tasks."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from vista.runtime.materialization import TaskManifest, TaskSpec

from .skillsbench import SkillsBenchSourceAdapter, SourceAuditError


@dataclass(slots=True)
class PreflightReport:
    ok: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    tasks: list[dict[str, Any]] = field(default_factory=list)
    expected_trial_count: int | None = None
    docker_builds: list[dict[str, Any]] = field(default_factory=list)

    def error(self, message: str) -> None:
        self.ok = False
        self.errors.append(message)


def run_preflight(
    project_root: str | Path,
    *,
    docker_build: bool = False,
    docker_timeout_sec: int = 900,
) -> PreflightReport:
    root = Path(project_root).resolve()
    report = PreflightReport()
    manifest_path = root / "tasks" / "manifest.yaml"
    if not manifest_path.is_file():
        report.error(f"missing task manifest: {manifest_path}")
        return report
    try:
        raw_manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        manifest = TaskManifest.load(manifest_path)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        report.error(f"invalid task manifest: {exc}")
        return report

    expected_tasks = int(raw_manifest.get("experiment", {}).get("expected_task_count", 6))
    trials_per = int(raw_manifest.get("experiment", {}).get("trials_per_task_condition", 3))
    conditions = tuple(raw_manifest.get("experiment", {}).get("conditions", ()))
    report.expected_trial_count = len(manifest.tasks) * len(conditions) * trials_per
    if len(manifest.tasks) != expected_tasks:
        report.error(f"expected {expected_tasks} tasks, found {len(manifest.tasks)}")
    declared_count = raw_manifest.get("experiment", {}).get("expected_trial_count")
    if declared_count is not None and int(declared_count) != report.expected_trial_count:
        report.error(
            "manifest trial count mismatch: "
            f"declared {declared_count}, computed {report.expected_trial_count}"
        )
    if tuple(conditions) != ("noskill", "native", "pd-vista"):
        report.error(f"pilot conditions must be noskill/native/pd-vista, found {conditions}")

    adapter = SkillsBenchSourceAdapter(root)
    for task in manifest.tasks:
        _audit_task(root, task, adapter, report)
        if docker_build:
            _docker_build(task, adapter, report, docker_timeout_sec)
    return report


def _audit_task(
    root: Path,
    task: TaskSpec,
    adapter: SkillsBenchSourceAdapter,
    report: PreflightReport,
) -> None:
    task_record: dict[str, Any] = {"task_id": task.task_id}
    try:
        audit = adapter.audit(task.source_task_path, task.source_skill_path)
    except SourceAuditError as exc:
        report.error(f"{task.task_id}: source audit could not run: {exc}")
        report.tasks.append(task_record)
        return
    task_record.update(
        {
            "source_task_path": audit.source_task_path.relative_to(root).as_posix(),
            "source_skill_path": audit.source_skill_path.relative_to(root).as_posix(),
            "skill_ids": list(audit.skill_ids),
            "skill_file_count": audit.file_count,
            "native_source_digest": audit.source_digest,
            "credential_hits": list(audit.credential_hits),
        }
    )
    report.tasks.append(task_record)
    for warning in audit.warnings:
        report.warnings.append(f"{task.task_id}: {warning}")
    for error in audit.errors:
        report.error(f"{task.task_id}: {error}")
    if audit.credential_hits:
        report.error(f"{task.task_id}: credential-like material found: {audit.credential_hits}")

    task_yaml = root / "tasks" / "pilot" / task.task_id / "task.yaml"
    if not task_yaml.is_file():
        report.error(f"{task.task_id}: missing pilot task metadata: {task_yaml}")
    else:
        _audit_pilot_metadata(task, task_yaml, report)
    pd_root = (root / (task.pd_vista_skill_path or "")).resolve()
    if not _inside(pd_root, root / "skills" / "pd-vista"):
        report.error(f"{task.task_id}: PD-VISTA path escapes skills/pd-vista: {pd_root}")
    else:
        _audit_pd_wrapper(task, pd_root, audit.skill_ids, report)


def _audit_pilot_metadata(task: TaskSpec, task_yaml: Path, report: PreflightReport) -> None:
    try:
        raw = yaml.safe_load(task_yaml.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        report.error(f"{task.task_id}: invalid pilot task metadata: {exc}")
        return
    if raw.get("mapping_status") != "resolved":
        report.error(f"{task.task_id}: mapping_status must be resolved")
    source = raw.get("source") or {}
    skill_paths = raw.get("skill_paths") or {}
    checks = (
        ("source.definition_path", source.get("definition_path"), task.source_task_path),
        ("source.skill_path", source.get("skill_path"), task.source_skill_path),
        ("skill_paths.native", skill_paths.get("native"), task.native_skill_path),
        ("skill_paths.pd_vista", skill_paths.get("pd_vista"), task.pd_vista_skill_path),
    )
    for label, declared, expected in checks:
        if declared != expected:
            report.error(
                f"{task.task_id}: {label} does not match tasks/manifest.yaml "
                f"(declared={declared!r}, expected={expected!r})"
            )


def _audit_pd_wrapper(
    task: TaskSpec,
    pd_root: Path,
    source_skill_ids: tuple[str, ...],
    report: PreflightReport,
) -> None:
    root_skill = pd_root / "SKILL.md"
    manifest_path = pd_root / "manifest.yaml"
    source_ref = pd_root / "source.yaml"
    for path in (root_skill, manifest_path, source_ref):
        if not path.is_file():
            report.error(f"{task.task_id}: missing PD-VISTA wrapper file: {path}")
    if not manifest_path.is_file():
        return
    try:
        raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        report.error(f"{task.task_id}: invalid PD-VISTA manifest: {exc}")
        return
    modules = raw.get("modules")
    if not isinstance(modules, list):
        report.error(f"{task.task_id}: PD-VISTA manifest modules must be a list")
        return
    module_ids = {str(item.get("id")) for item in modules if isinstance(item, dict)}
    if module_ids != set(source_skill_ids):
        report.error(
            f"{task.task_id}: PD-VISTA module set differs from native source: "
            f"wrapper={sorted(module_ids)}, source={list(source_skill_ids)}"
        )
    if not raw.get("source_content_equivalence") == "byte-for-byte-under-modules":
        report.error(f"{task.task_id}: PD-VISTA wrapper lacks byte-equivalence declaration")
    for item in modules:
        if not isinstance(item, dict):
            report.error(f"{task.task_id}: malformed PD-VISTA module entry")
            continue
        module_id = str(item.get("id", ""))
        declared_path = str(item.get("path", ""))
        expected = f"modules/{module_id}/SKILL.md"
        if declared_path != expected:
            report.error(
                f"{task.task_id}: module {module_id!r} must route to {expected!r}, "
                f"found {declared_path!r}"
            )


def _docker_build(
    task: TaskSpec,
    adapter: SkillsBenchSourceAdapter,
    report: PreflightReport,
    timeout_sec: int,
) -> None:
    try:
        audit = adapter.audit(task.source_task_path, task.source_skill_path)
        audit.raise_for_errors()
    except SourceAuditError as exc:
        report.error(f"{task.task_id}: Docker build skipped after source audit failure: {exc}")
        return
    dockerfile = audit.source_task_path / "environment" / "Dockerfile"
    started = time.monotonic()
    try:
        completed = subprocess.run(
            [
                "docker",
                "build",
                "--file",
                str(dockerfile),
                "--tag",
                f"vista-preflight-{task.task_id}",
                str(audit.source_task_path),
            ],
            cwd=str(adapter.project_root),
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
        report.docker_builds.append(
            {
                "task_id": task.task_id,
                "exit_code": completed.returncode,
                "elapsed_sec": round(time.monotonic() - started, 2),
                "last_output": (completed.stdout + completed.stderr)[-1200:],
            }
        )
        if completed.returncode != 0:
            report.error(
                f"{task.task_id}: Docker build failed with exit code {completed.returncode}"
            )
    except FileNotFoundError:
        report.error(
            "Docker executable not found; rerun without -DockerBuild or install Docker Desktop"
        )
    except subprocess.TimeoutExpired:
        report.error(f"{task.task_id}: Docker build exceeded {timeout_sec}s")


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--docker-build", action="store_true")
    parser.add_argument("--docker-timeout-sec", type=int, default=900)
    parser.add_argument("--report")
    args = parser.parse_args(argv)
    report = run_preflight(
        args.project_root,
        docker_build=args.docker_build,
        docker_timeout_sec=args.docker_timeout_sec,
    )
    payload = json.dumps(asdict(report), ensure_ascii=False, indent=2)
    if args.report:
        report_path = (Path(args.project_root).resolve() / args.report).resolve()
        if not _inside(report_path, Path(args.project_root).resolve() / "reports"):
            parser.error("--report must be inside reports/")
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
