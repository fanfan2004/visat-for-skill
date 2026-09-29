from __future__ import annotations

import hashlib
import io
import json
import subprocess
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from experiments.formal import build_images
from experiments.formal.build_images import (
    artifact_metadata,
    artifact_trust_evidence,
    artifact_version_probe,
    command_result,
    docker_build_result,
)
from experiments.formal.readiness import (
    image_identity_matches,
    readiness_outcome,
    readiness_stable,
)
from vista.runtime.opencode_image import (
    OpenCodeArtifact,
    OpenCodeImageContractError,
    load_task_image_contract,
    prepare_opencode_artifact,
    prepare_opencode_artifact_from_verified_source,
    stage_task_image_context,
    task_build_context_sha256,
)


def test_build_refuses_unverifiable_expected_digest_source() -> None:
    args = type(
        "Args",
        (),
        {
            "official_cli_checksum_status": "unavailable",
            "historical_pilot_container_status": "not_found",
            "opencode_source_url": "https://github.com/anomalyco/opencode/releases/download/v1.18.13/opencode-linux-x64.tar.gz",
        },
    )()

    evidence = artifact_trust_evidence(args)

    assert evidence["status"] == "BLOCKED_TRUST_MISSING"
    assert evidence["download_allowed_only_after_expected_digest"] is True
    assert evidence["download_attempted"] is False
    assert evidence["historical_pilot_container"]


def test_official_archive_digest_is_trust_source_without_binary_digest() -> None:
    args = type(
        "Args",
        (),
        {
            "official_cli_checksum_status": "verified",
            "historical_pilot_container_status": "not_found",
            "opencode_source_url": "https://github.com/anomalyco/opencode/releases/download/v1.18.13/opencode-linux-x64.tar.gz",
            "opencode_expected_source_sha256": (
                "8d500b20fed2d26e537e221895b1a575476571b4f0089bb29fb13eeb8eb9e937"
            ),
            "opencode_expected_sha256": None,
        },
    )()

    evidence = artifact_trust_evidence(args)

    assert evidence["status"] == "EXPECTED_DIGEST_SOURCE_DECLARED"
    assert evidence["official_archive_digest_kind"] == "archive_sha256"
    assert evidence["official_archive_sha256"] == args.opencode_expected_source_sha256
    assert evidence["historical_pilot_container_status"] == "not_found"


def test_artifact_metadata_keeps_archive_and_binary_digests_distinct(
    tmp_path: Path,
) -> None:
    binary = b"derived-binary"
    archive_buffer = io.BytesIO()
    with tarfile.open(fileobj=archive_buffer, mode="w:gz") as archive:
        info = tarfile.TarInfo("opencode")
        info.size = len(binary)
        archive.addfile(info, io.BytesIO(binary))
    source_digest = hashlib.sha256(archive_buffer.getvalue()).hexdigest()
    artifact = prepare_opencode_artifact_from_verified_source(
        artifact_root=tmp_path / "artifacts",
        source_url="https://example.invalid/archive",
        expected_source_sha256=source_digest,
        archive_member="opencode",
        downloader=lambda _source: archive_buffer.getvalue(),
    )
    args = type(
        "Args",
        (),
        {
            "opencode_source_url": "https://example.invalid/archive",
            "opencode_archive_member": "opencode",
            "opencode_expected_sha256": None,
            "opencode_expected_source_sha256": source_digest,
        },
    )()

    metadata = artifact_metadata(artifact, args)

    assert metadata["source_digest_kind"] == "archive_sha256"
    assert metadata["expected_source_sha256"] == source_digest
    assert metadata["source_sha256"] == source_digest
    assert metadata["expected_binary_sha256"] == artifact.actual_binary_sha256
    assert metadata["expected_binary_sha256"] != source_digest


def test_extracted_binary_version_probe_is_model_free_and_version_gated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = prepare_opencode_artifact(
        artifact_root=tmp_path / "artifact-cache",
        source_url="https://example.invalid/binary",
        expected_binary_sha256=hashlib.sha256(b"binary").hexdigest(),
        downloader=lambda _source: b"binary",
    )
    seen: list[list[str]] = []

    def fake_command(command: list[str], timeout_sec: int = 30) -> dict[str, object]:
        seen.append(command)
        assert timeout_sec == 30
        return {"command": command, "return_code": 0, "stdout": "1.18.13\n", "stderr": ""}

    monkeypatch.setattr("experiments.formal.build_images.REPO_ROOT", tmp_path)
    monkeypatch.setattr("experiments.formal.build_images.command_result", fake_command)
    probe = artifact_version_probe(artifact)
    relative = artifact.binary_path.resolve().relative_to(tmp_path.resolve()).as_posix()

    assert probe["actual_version_match"] is True
    assert seen == [
        [
            "wsl.exe",
            "-e",
            "sh",
            "-lc",
            f"set -eu; /mnt/d/vista/{relative} --version",
        ]
    ]


def test_build_command_output_is_decoded_as_utf8_with_replacement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        seen.update(kwargs)
        return subprocess.CompletedProcess(command, 0, stdout="构建完成\n", stderr="")

    monkeypatch.setattr("experiments.formal.build_images.subprocess.run", fake_run)

    result = command_result(["docker", "build"], timeout_sec=5)

    assert result["return_code"] == 0
    assert result["stdout"] == "构建完成\n"
    assert seen["encoding"] == "utf-8"
    assert seen["errors"] == "replace"


def test_build_execute_uses_source_only_artifact_path_without_binary_sha(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_digest = build_images.OFFICIAL_ARCHIVE_SHA256
    artifact_path = tmp_path / "data" / "artifacts" / "opencode-1.18.13-linux-x64"
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_bytes(b"verified-binary")
    binary_digest = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
    artifact = OpenCodeArtifact(
        version="1.18.13",
        platform="linux-x64",
        source_url=build_images.DEFAULT_SOURCE_URL,
        binary_path=artifact_path,
        expected_binary_sha256=binary_digest,
        actual_binary_sha256=binary_digest,
        source_sha256=source_digest,
        reused=False,
        source_digest_kind="archive_sha256",
    )
    task_entries = []
    for task_id in build_images.TASK_IDS:
        task_root = tmp_path / "tasks" / task_id
        (task_root / "environment").mkdir(parents=True)
        (task_root / "verifier").mkdir()
        (task_root / "task.md").write_text(task_id, encoding="utf-8")
        (task_root / "environment" / "Dockerfile").write_text(
            "FROM scratch\nCOPY payload.txt /payload.txt\n", encoding="utf-8"
        )
        (task_root / "environment" / "payload.txt").write_text(task_id, encoding="utf-8")
        (task_root / "verifier" / "verify.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        task_entries.append(
            {
                "task_id": task_id,
                "native_skill_path": f"tasks/{task_id}",
                "pd_vista_skill_path": f"tasks/{task_id}",
                "source_task_path": f"tasks/{task_id}",
            }
        )
    (tmp_path / "tasks" / "manifest.yaml").write_text(
        __import__("yaml").safe_dump({"tasks": task_entries}, sort_keys=False),
        encoding="utf-8",
    )

    prepare_calls = 0

    def fake_prepare(**_kwargs: object) -> OpenCodeArtifact:
        nonlocal prepare_calls
        prepare_calls += 1
        return artifact

    def fake_build(record: dict[str, object], *, build_context: Path) -> None:
        digest_tail = str(record["task_id"])[-1] * 64
        record.update(
            {
                "image_status": "qualified",
                "image_tag": f"vista-{record['task_id']}",
                "image_digest": "sha256:" + digest_tail,
                "immutable_image_ref": f"vista-{record['task_id']}@sha256:{digest_tail}",
                "build_result": {"return_code": 0, "command": ["fake-docker"]},
            }
        )

    monkeypatch.setattr(build_images, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(
        build_images,
        "docker_snapshot",
        lambda: {
            "daemon_reachable": True,
            "competition_risk": False,
            "active_containers": [],
            "unrelated_milvus_containers": [],
        },
    )
    monkeypatch.setattr(build_images, "vendor_commit", lambda: "vendor-commit")
    monkeypatch.setattr(
        build_images,
        "benchflow_identity",
        lambda: {"version": "0.6.5", "source_tree_sha256": "benchflow"},
    )
    monkeypatch.setattr(
        build_images,
        "prepare_opencode_artifact_from_verified_source",
        fake_prepare,
    )
    monkeypatch.setattr(
        build_images,
        "artifact_version_probe",
        lambda _artifact: {"return_code": 0, "actual_version_match": True},
    )
    monkeypatch.setattr(build_images, "build_one_image", fake_build)

    args = type(
        "Args",
        (),
        {
            "report_id": "source-only-build-test",
            "build_id": "source-only-build-test",
            "execute": True,
            "prepare_artifact": True,
            "opencode_source_url": build_images.DEFAULT_SOURCE_URL,
            "opencode_expected_sha256": None,
            "opencode_expected_source_sha256": source_digest,
            "opencode_archive_member": "opencode",
            "resource_window_approved": "test-window",
            "allow_shared_docker": False,
            "official_cli_checksum_status": "verified",
            "historical_pilot_container_status": "not_found",
        },
    )()

    report = build_images.execute(args)

    assert prepare_calls == 1
    assert report["opencode"]["status"] == "verified"
    assert report["opencode"]["expected_binary_sha256"] == binary_digest
    assert all(task["image_status"] == "qualified" for task in report["tasks"])


def test_docker_build_result_redirects_output_to_project_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_popen(command: list[str], **kwargs: object) -> SimpleNamespace:
        assert command == ["docker", "build"]
        stream = kwargs["stdout"]
        assert hasattr(stream, "write")
        stream.write("build output\n")
        return SimpleNamespace(poll=lambda: 0)

    monkeypatch.setattr(build_images.subprocess, "Popen", fake_popen)
    log_path = tmp_path / "docker-build.log"

    result = docker_build_result(["docker", "build"], log_path=log_path, timeout_sec=30)

    assert result["return_code"] == 0
    assert result["log_path"] == str(log_path)
    assert "build output" in result["stdout"]
    assert log_path.read_text(encoding="utf-8") == "build output\n"


def test_docker_build_result_recovers_after_buildkit_done_cli_hang(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    iidfile = tmp_path / "image.iid"

    class HangingProcess:
        def __init__(self, stdout: object) -> None:
            self.stdout = stdout
            self.terminated = False
            stdout.write("#17 DONE\n")
            stdout.flush()
            iidfile.write_text("sha256:" + "a" * 64, encoding="utf-8")

        def poll(self) -> None:
            return 0 if self.terminated else None

        def terminate(self) -> None:
            self.terminated = True

        def wait(self, timeout: float | None = None) -> int:
            return 0

    def fake_popen(command: list[str], **kwargs: object) -> HangingProcess:
        assert command == ["docker", "build"]
        return HangingProcess(kwargs["stdout"])

    monkeypatch.setattr(build_images.subprocess, "Popen", fake_popen)

    result = docker_build_result(
        ["docker", "build"],
        log_path=tmp_path / "docker-build.log",
        iidfile=iidfile,
        timeout_sec=30,
        done_grace_sec=0,
    )

    assert result["return_code"] == 0
    assert result["recovered_after_buildkit_done"] is True


def test_readiness_requires_container_frozen_image_identity() -> None:
    expected = "sha256:" + "a" * 64
    assert image_identity_matches(expected, {"image_id_digest": expected})
    assert image_identity_matches(
        expected, {"config_image": "vista-task-001@" + expected}
    )
    assert not image_identity_matches(expected, {"image_id_digest": "sha256:" + "b" * 64})


def test_subset_readiness_cannot_upgrade_six_task_formal_protocol() -> None:
    selected = ["task-001", "task-002", "task-004", "task-006"]
    tasks = [{"task_id": task_id, "status": "GO"} for task_id in selected]

    assert readiness_outcome(selected, tasks) == "GO_FOR_SUBSET_ZERO_MODEL_READINESS"


def test_subset_readiness_still_fails_when_one_selected_task_fails() -> None:
    selected = ["task-001", "task-002", "task-004", "task-006"]
    tasks = [{"task_id": task_id, "status": "GO"} for task_id in selected]
    tasks[-1]["status"] = "NO-GO"

    assert readiness_outcome(selected, tasks) == "NO-GO"


def test_readiness_stability_uses_public_verifier_class_metadata() -> None:
    runs = [
        {
            "verifier_class": "task_failure",
            "quarantine": False,
            "timed_out": False,
            "duration_ms": 1000,
        },
        {
            "verifier_class": "task_failure",
            "quarantine": False,
            "timed_out": False,
            "duration_ms": 900,
        },
    ]

    assert readiness_stable(runs, verifier_timeout_sec=10)


def test_staged_context_digest_ignores_only_post_build_audit_outputs(tmp_path: Path) -> None:
    (tmp_path / "B.txt").write_text("B", encoding="utf-8")
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    before = task_build_context_sha256(tmp_path)

    (tmp_path / "image.iid").write_text("sha256:example", encoding="utf-8")
    (tmp_path / "docker-build.log").write_text("build output", encoding="utf-8")
    assert task_build_context_sha256(tmp_path) == before

    (tmp_path / "a.txt").write_text("changed", encoding="utf-8")
    assert task_build_context_sha256(tmp_path) != before


def test_task_context_injects_verified_binary_without_cross_task_files(tmp_path: Path) -> None:
    source = tmp_path / "vendor" / "skillsbench" / "tasks" / "task-a"
    (source / "environment").mkdir(parents=True)
    (source / "verifier").mkdir()
    (source / "oracle").mkdir()
    (source / "task.md").write_text("task", encoding="utf-8")
    dockerfile = source / "environment" / "Dockerfile"
    dockerfile.write_text(
        "FROM ubuntu:24.04\n"
        "RUN apt-get update\n"
        "RUN curl -fL https://github.com/coursier/coursier/releases/download/"
        "v2.1.25-M23/cs-x86_64-pc-linux.gz && ./cs setup --yes "
        "&& ./cs install scala:2.13.12\n"
        "COPY app.txt /root/app.txt\n",
        encoding="utf-8",
    )
    (source / "environment" / "app.txt").write_text("task-a", encoding="utf-8")
    (source / "verifier" / "test.sh").write_text(
        "#!/bin/sh\n"
        "curl -fL https://github.com/coursier/coursier/releases/download/"
        "v2.1.25-M23/cs-x86_64-pc-linux.gz | gzip -d > cs "
        "&& chmod +x cs && ./cs setup --yes\n",
        encoding="utf-8",
    )
    (source / "verifier" / "test_outputs.py").write_text(
        "# This verifier invokes sbt for the public test suite.\n"
        "requires_sbt = True\n",
        encoding="utf-8",
    )
    (source / "oracle" / "secret.txt").write_text("hidden", encoding="utf-8")
    artifact = tmp_path / "opencode"
    artifact.write_bytes(b"verified-opencode")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()

    context = stage_task_image_context(
        source_task=source,
        project_root=tmp_path,
        task_id="task-001",
        build_id="qualification-red",
        artifact_path=artifact,
        opencode_version="1.18.13",
        opencode_digest=digest,
    )

    staged_dockerfile = (context.stage_root / "environment" / "Dockerfile").read_text(
        encoding="utf-8"
    )
    assert staged_dockerfile.startswith("FROM ubuntu:24.04\n")
    assert "http://mirrors.aliyun.com/ubuntu" in staged_dockerfile
    assert staged_dockerfile.index("http://mirrors.aliyun.com/ubuntu") < staged_dockerfile.index(
        "apt-get -o Acquire::Retries=5"
    )
    assert "https://downloads.lightbend.com/scala/2.13.12/scala-2.13.12.tgz" in staged_dockerfile
    assert "afe7e6fb59e3c88457e99f7018730e52fba27c8d15e3d7154f72968206ca8719" in staged_dockerfile
    assert "./cs setup" not in staged_dockerfile
    assert "./cs install" not in staged_dockerfile
    assert (
        "RUN apt-get -o Acquire::Retries=5 -o Acquire::http::Timeout=60 update"
        in staged_dockerfile
    )
    assert "RUN curl --fail --location --retry 5" in staged_dockerfile
    assert "COPY _vista_tool_layer/opencode /usr/local/bin/opencode" in staged_dockerfile
    assert digest in staged_dockerfile
    assert (
        context.stage_root / "environment" / "_vista_tool_layer" / "opencode"
    ).read_bytes() == artifact.read_bytes()
    assert (
        context.stage_root / "environment" / "app.txt"
    ).read_text(encoding="utf-8") == "task-a"
    staged_verifier = (context.stage_root / "verifier" / "test.sh").read_text(encoding="utf-8")
    assert "scala -version && scalac -version" not in staged_verifier
    assert "coursier/releases/download" not in staged_verifier
    assert "command -v sbt && sbt --script-version" in staged_verifier
    assert (
        "https://github.com/sbt/sbt/releases/download/v1.12.0/sbt-1.12.0.tgz"
        in staged_dockerfile
    )
    assert "e4ade3f4bdbe1e7445be9acf02cb1b3574beb460362e17287e246b7eaf9bb952" in staged_dockerfile
    assert "RUN cd /root/localtest && sbt -batch update" in staged_dockerfile
    assert not (context.stage_root / "oracle").exists()
    assert context.vendor_dockerfile_digest == hashlib.sha256(
        dockerfile.read_bytes()
    ).hexdigest()
    assert context.staged_dockerfile_digest != context.vendor_dockerfile_digest


def test_task_context_normalizes_environment_shell_line_endings(tmp_path: Path) -> None:
    source = tmp_path / "vendor" / "skillsbench" / "tasks" / "task-a"
    (source / "environment").mkdir(parents=True)
    (source / "verifier").mkdir()
    (source / "task.md").write_text("task", encoding="utf-8")
    (source / "environment" / "Dockerfile").write_text(
        "FROM ubuntu:24.04\n",
        encoding="utf-8",
    )
    (source / "environment" / "download_papers.sh").write_bytes(
        b"#!/usr/bin/env bash\r\nset -euo pipefail\r\n"
    )
    artifact = tmp_path / "opencode"
    artifact.write_bytes(b"verified-opencode")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()

    context = stage_task_image_context(
        source_task=source,
        project_root=tmp_path,
        task_id="task-001",
        build_id="qualification-line-endings",
        artifact_path=artifact,
        opencode_version="1.18.13",
        opencode_digest=digest,
    )

    staged_script = (
        context.stage_root / "environment" / "download_papers.sh"
    ).read_bytes()
    assert staged_script == b"#!/usr/bin/env bash\nset -euo pipefail\n"


def test_task_context_adds_arxiv_download_fallback(tmp_path: Path) -> None:
    source = tmp_path / "vendor" / "skillsbench" / "tasks" / "task-a"
    (source / "environment").mkdir(parents=True)
    (source / "verifier").mkdir()
    (source / "task.md").write_text("task", encoding="utf-8")
    download_block = (
        '        wget -q \\\n'
        '            --retry-connrefused \\\n'
        '            --waitretry=1 \\\n'
        '            --tries=5 \\\n'
        '            --timeout=30 \\\n'
        '            -O "$paper" "$url"\n'
    )
    (source / "environment" / "Dockerfile").write_text(
        "FROM ubuntu:24.04\nRUN <<'EOF'\n" + download_block + "EOF\n",
        encoding="utf-8",
    )
    (source / "environment" / "download_papers.sh").write_text(
        "#!/usr/bin/env bash\n" + download_block,
        encoding="utf-8",
    )
    artifact = tmp_path / "opencode"
    artifact.write_bytes(b"verified-opencode")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()

    context = stage_task_image_context(
        source_task=source,
        project_root=tmp_path,
        task_id="task-005",
        build_id="qualification-arxiv-fallback",
        artifact_path=artifact,
        opencode_version="1.18.13",
        opencode_digest=digest,
    )

    staged_dockerfile = (context.stage_root / "environment" / "Dockerfile").read_text(
        encoding="utf-8"
    )
    staged_script = (
        context.stage_root / "environment" / "download_papers.sh"
    ).read_text(encoding="utf-8")
    for staged_text in (staged_dockerfile, staged_script):
        assert "https:\\/\\/export.arxiv.org" in staged_text
        assert '--user-agent="Mozilla/5.0"' in staged_text
        assert 'test "$(head -c 4 "$paper")" = "%PDF"' in staged_text


def test_task_context_materializes_arxiv_corpus_for_execute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "vendor" / "skillsbench" / "tasks" / "task-a"
    (source / "environment").mkdir(parents=True)
    (source / "verifier").mkdir()
    (source / "task.md").write_text("task", encoding="utf-8")
    urls = [f"https://arxiv.org/pdf/2401.{index:05d}v1.pdf" for index in range(100)]
    (source / "environment" / "download_papers.sh").write_text(
        "#!/usr/bin/env bash\nurls=\"\n" + "\n".join(urls) + "\n\"\n",
        encoding="utf-8",
    )
    (source / "environment" / "Dockerfile").write_text(
        "FROM ubuntu:24.04\n"
        "RUN <<'EOF'\nset -euo pipefail\necho download\nEOF\n"
        "COPY download_papers.sh /tmp/download_papers.sh\n"
        "RUN bash /tmp/download_papers.sh && rm /tmp/download_papers.sh\n",
        encoding="utf-8",
    )
    artifact = tmp_path / "opencode"
    artifact.write_bytes(b"verified-opencode")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()

    class FakeResponse:
        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return b"%PDF-1.7\nformal corpus"

    monkeypatch.setattr(
        "vista.runtime.opencode_image.urlopen",
        lambda *args, **kwargs: FakeResponse(),
    )

    context = stage_task_image_context(
        source_task=source,
        project_root=tmp_path,
        task_id="task-005",
        build_id="qualification-arxiv-materialized",
        artifact_path=artifact,
        opencode_version="1.18.13",
        opencode_digest=digest,
        prepare_external_artifacts=True,
    )

    environment = context.stage_root / "environment"
    papers = list((environment / "_vista_papers").glob("*.pdf"))
    staged_dockerfile = (environment / "Dockerfile").read_text(encoding="utf-8")
    assert len(papers) == 100
    assert all(path.read_bytes().startswith(b"%PDF") for path in papers)
    assert "COPY _vista_papers /root/papers/all" in staged_dockerfile
    assert "RUN <<'EOF'" not in staged_dockerfile
    assert "RUN bash /tmp/download_papers.sh" not in staged_dockerfile


def test_formal_image_registry_rejects_pending_digest(tmp_path: Path) -> None:
    manifest = tmp_path / "images.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "tasks": {
                    "task-001": {
                        "status": "pending_qualification",
                        "image_tag": "vista-task-001",
                        "image_digest": None,
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(OpenCodeImageContractError, match="immutable image digest"):
        load_task_image_contract(manifest, "task-001")


def test_formal_image_registry_returns_digest_pinned_reference(tmp_path: Path) -> None:
    manifest = tmp_path / "images.json"
    digest = "sha256:" + "d" * 64
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "tasks": {
                    "task-001": {
                        "status": "qualified",
                        "image_tag": "vista-task-001",
                        "image_digest": digest,
                        "stage_root": "data/raw/_opencode_builds/qualification/tasks/task-001",
                        "source_task_digest": "a" * 64,
                        "vendor_dockerfile_digest": "b" * 64,
                        "staged_dockerfile_digest": "c" * 64,
                        "build_context_digest": "e" * 64,
                        "opencode_version": "1.18.13",
                        "opencode_binary_digest": "f" * 64,
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    contract = load_task_image_contract(manifest, "task-001")
    assert contract.immutable_image_ref == "vista-task-001@" + digest


def test_all_six_task_contexts_keep_vendor_dependencies_separate(tmp_path: Path) -> None:
    project = Path(__file__).resolve().parents[1]
    task_sources = {
        "task-001": "python-scala-translation",
        "task-002": "enterprise-information-search",
        "task-003": "data-to-d3",
        "task-004": "powerlifting-coef-calc",
        "task-005": "organize-messy-files",
        "task-006": "fix-build-agentops",
    }
    vendor_digests = {
        task_id: hashlib.sha256(
            (
                project
                / "vendor"
                / "skillsbench"
                / "tasks"
                / source
                / "environment"
                / "Dockerfile"
            ).read_bytes()
        ).hexdigest()
        for task_id, source in task_sources.items()
    }
    artifact = tmp_path / "opencode"
    artifact.write_bytes(b"one-verified-tool-layer")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()

    contexts = [
        stage_task_image_context(
            source_task=project / "vendor" / "skillsbench" / "tasks" / source,
            project_root=tmp_path,
            task_id=task_id,
            build_id="qualification-six-static",
            artifact_path=artifact,
            opencode_digest=digest,
        )
        for task_id, source in task_sources.items()
    ]

    assert len({context.stage_root for context in contexts}) == 6
    assert {context.opencode_binary_digest for context in contexts} == {digest}
    for context in contexts:
        dockerfile = (
            context.stage_root / "environment" / "Dockerfile"
        ).read_text(encoding="utf-8")
        assert "COPY _vista_tool_layer/opencode /usr/local/bin/opencode" in dockerfile
        assert digest in dockerfile
        assert context.vendor_dockerfile_digest == vendor_digests[context.task_id]
        assert (context.stage_root / "verifier").is_dir()
    task006_verifier = (
        contexts[-1].stage_root / "verifier" / "test.sh"
    ).read_text(encoding="utf-8")
    assert "export TOX_WORK_DIR=/tmp/vista-tox-work" in task006_verifier
    task006_dockerfile = (
        contexts[-1].stage_root / "environment" / "Dockerfile"
    ).read_text(encoding="utf-8")
    assert "tox==4.58.0" in task006_dockerfile
    assert "-e py38,py39,py310,py311,py312 --notest" in task006_dockerfile
    for context in contexts[:-1]:
        dockerfile = (
            context.stage_root / "environment" / "Dockerfile"
        ).read_text(encoding="utf-8")
        assert "VISTA task-006 verifier dependency prewarm" not in dockerfile


def test_artifact_store_refuses_mismatched_existing_binary_without_download(
    tmp_path: Path,
) -> None:
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    existing = artifact_root / "opencode-1.18.13-linux-x64"
    existing.write_bytes(b"wrong-existing-binary")
    expected = hashlib.sha256(b"verified-binary").hexdigest()
    downloads = 0

    def downloader(_source: str) -> bytes:
        nonlocal downloads
        downloads += 1
        return b"verified-binary"

    with pytest.raises(OpenCodeImageContractError, match="refusing to overwrite"):
        prepare_opencode_artifact(
            artifact_root=artifact_root,
            source_url="https://example.invalid/opencode",
            expected_binary_sha256=expected,
            downloader=downloader,
        )
    assert downloads == 0
    assert existing.read_bytes() == b"wrong-existing-binary"


def test_artifact_store_prepares_once_and_reuses_version_digest_match(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts"
    binary = b"verified-binary"
    expected = hashlib.sha256(binary).hexdigest()
    downloads = 0

    def downloader(_source: str) -> bytes:
        nonlocal downloads
        downloads += 1
        return binary

    first = prepare_opencode_artifact(
        artifact_root=artifact_root,
        source_url="https://example.invalid/opencode",
        expected_binary_sha256=expected,
        downloader=downloader,
    )
    second = prepare_opencode_artifact(
        artifact_root=artifact_root,
        source_url="https://example.invalid/opencode",
        expected_binary_sha256=expected,
        downloader=lambda _source: (_ for _ in ()).throw(AssertionError("redownload")),
    )

    assert first.reused is False
    assert second.reused is True
    assert downloads == 1
    manifest = artifact_root / "opencode-1.18.13-linux-x64.json"
    assert json.loads(manifest.read_text(encoding="utf-8"))["status"] == "verified"


def test_artifact_store_can_verify_a_release_archive_member(tmp_path: Path) -> None:
    binary = b"verified-archive-binary"
    archive_buffer = io.BytesIO()
    with tarfile.open(fileobj=archive_buffer, mode="w:gz") as archive:
        info = tarfile.TarInfo("opencode")
        info.size = len(binary)
        archive.addfile(info, io.BytesIO(binary))

    artifact = prepare_opencode_artifact(
        artifact_root=tmp_path / "artifacts",
        source_url="https://example.invalid/opencode.tar.gz",
        expected_binary_sha256=hashlib.sha256(binary).hexdigest(),
        archive_member="opencode",
        downloader=lambda _source: archive_buffer.getvalue(),
    )
    assert artifact.binary_path.read_bytes() == binary


def test_artifact_store_derives_binary_digest_from_verified_archive_source(
    tmp_path: Path,
) -> None:
    binary = b"official-release-binary"
    archive_buffer = io.BytesIO()
    with tarfile.open(fileobj=archive_buffer, mode="w:gz") as archive:
        info = tarfile.TarInfo("opencode")
        info.size = len(binary)
        archive.addfile(info, io.BytesIO(binary))
    payload = archive_buffer.getvalue()
    expected_source = hashlib.sha256(payload).hexdigest()
    expected_binary = hashlib.sha256(binary).hexdigest()

    artifact = prepare_opencode_artifact_from_verified_source(
        artifact_root=tmp_path / "artifacts",
        source_url="https://example.invalid/opencode-linux-x64.tar.gz",
        expected_source_sha256=expected_source,
        archive_member="opencode",
        downloader=lambda _source: payload,
    )

    manifest = json.loads(
        (tmp_path / "artifacts" / "opencode-1.18.13-linux-x64.json").read_text(
            encoding="utf-8"
        )
    )
    assert artifact.actual_binary_sha256 == expected_binary
    assert artifact.expected_binary_sha256 == expected_binary
    assert artifact.source_sha256 == expected_source
    assert artifact.source_digest_kind == "archive_sha256"
    assert manifest["expected_source_sha256"] == expected_source
    assert manifest["source_sha256"] == expected_source
    assert manifest["expected_binary_sha256"] == expected_binary
    assert manifest["actual_binary_sha256"] == expected_binary
    assert manifest["source_digest_kind"] == "archive_sha256"
    assert manifest["status"] == "verified"


def test_artifact_store_rejects_archive_source_before_writing_binary(tmp_path: Path) -> None:
    payload = b"not-the-official-archive"
    downloads = 0

    def downloader(_source: str) -> bytes:
        nonlocal downloads
        downloads += 1
        return payload

    with pytest.raises(OpenCodeImageContractError, match="source digest mismatch"):
        prepare_opencode_artifact_from_verified_source(
            artifact_root=tmp_path / "artifacts",
            source_url="https://example.invalid/opencode-linux-x64.tar.gz",
            expected_source_sha256="0" * 64,
            archive_member="opencode",
            downloader=downloader,
        )

    assert downloads == 1
    assert not list((tmp_path / "artifacts").glob("opencode-1.18.13-linux-x64*"))


def test_artifact_store_reuses_source_verified_archive_without_redownload(
    tmp_path: Path,
) -> None:
    binary = b"reusable-official-binary"
    archive_buffer = io.BytesIO()
    with tarfile.open(fileobj=archive_buffer, mode="w:gz") as archive:
        info = tarfile.TarInfo("opencode")
        info.size = len(binary)
        archive.addfile(info, io.BytesIO(binary))
    payload = archive_buffer.getvalue()
    source_digest = hashlib.sha256(payload).hexdigest()
    first = prepare_opencode_artifact_from_verified_source(
        artifact_root=tmp_path / "artifacts",
        source_url="https://example.invalid/archive",
        expected_source_sha256=source_digest,
        archive_member="opencode",
        downloader=lambda _source: payload,
    )

    second = prepare_opencode_artifact_from_verified_source(
        artifact_root=tmp_path / "artifacts",
        source_url="https://example.invalid/archive",
        expected_source_sha256=source_digest,
        archive_member="opencode",
        downloader=lambda _source: (_ for _ in ()).throw(AssertionError("redownload")),
    )

    assert first.reused is False
    assert second.reused is True
    assert second.actual_binary_sha256 == hashlib.sha256(binary).hexdigest()
