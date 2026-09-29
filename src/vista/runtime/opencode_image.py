"""Build-time OpenCode image contract and runtime identity probe.

The formal protocol treats OpenCode as an image dependency.  This module keeps
the runtime boundary deliberately small: a container may identify the binary,
but it may never download or install it during a trial.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tarfile
import time
import uuid
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

OPENCODE_VERSION = "1.18.13"
OPENCODE_BINARY_PATH = "/usr/local/bin/opencode"
OPENCODE_IDENTITY_PROBE_COMMAND = (
    "set -eu; command -v opencode; opencode --version; "
    "sha256sum /usr/local/bin/opencode"
)

_DIGEST_RE = re.compile(r"\b([0-9a-fA-F]{64})\b")
_VERSION_RE = re.compile(r"\b(\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?)\b")
_APT_RETRY_OPTIONS = "-o Acquire::Retries=5 -o Acquire::http::Timeout=60"
_APT_MIRROR_SETUP = (
    "RUN for source_file in /etc/apt/sources.list /etc/apt/sources.list.d/ubuntu.sources; "
    "do if [ -f \"$source_file\" ]; then "
    "sed -i 's|http://archive.ubuntu.com/ubuntu|http://mirrors.aliyun.com/ubuntu|g; "
    "s|http://security.ubuntu.com/ubuntu|http://mirrors.aliyun.com/ubuntu|g' "
    '"$source_file"; fi; done\n'
)
_SCALA_VERSION = "2.13.12"
_SCALA_ARCHIVE_SHA256 = "afe7e6fb59e3c88457e99f7018730e52fba27c8d15e3d7154f72968206ca8719"
_SCALA_INSTALL = (
    "RUN curl --fail --location --retry 5 --retry-all-errors --retry-delay 2 "
    f"https://downloads.lightbend.com/scala/{_SCALA_VERSION}/"
    f"scala-{_SCALA_VERSION}.tgz --output /tmp/scala.tgz && \\\n"
    f"    printf '{_SCALA_ARCHIVE_SHA256}  /tmp/scala.tgz\\n' | sha256sum -c - && \\\n"
    f"    tar -xzf /tmp/scala.tgz -C /opt && \\\n"
    f"    ln -s /opt/scala-{_SCALA_VERSION}/bin/scala /usr/local/bin/scala && \\\n"
    f"    ln -s /opt/scala-{_SCALA_VERSION}/bin/scalac /usr/local/bin/scalac && \\\n"
    "    rm /tmp/scala.tgz\n"
)
_SBT_VERSION = "1.12.0"
_SBT_ARCHIVE_SHA256 = "e4ade3f4bdbe1e7445be9acf02cb1b3574beb460362e17287e246b7eaf9bb952"
_SBT_INSTALL = (
    "RUN curl --fail --location --retry 5 --retry-all-errors --retry-delay 2 "
    f"https://github.com/sbt/sbt/releases/download/v{_SBT_VERSION}/"
    f"sbt-{_SBT_VERSION}.tgz --output /tmp/sbt.tgz && \\\n"
    f"    printf '{_SBT_ARCHIVE_SHA256}  /tmp/sbt.tgz\\n' | sha256sum -c - && \\\n"
    "    tar -xzf /tmp/sbt.tgz -C /opt && \\\n"
    "    ln -s /opt/sbt/bin/sbt /usr/local/bin/sbt && \\\n"
    "    rm /tmp/sbt.tgz\n"
)
_ARXIV_WGET_BLOCK = """        wget -q \\
            --retry-connrefused \\
            --waitretry=1 \\
            --tries=5 \\
            --timeout=30 \\
            -O "$paper" "$url"
"""
_ARXIV_WGET_WITH_FALLBACK = """        if ! wget -q \\
            --retry-connrefused \\
            --waitretry=2 \\
            --tries=5 \\
            --timeout=60 \\
            --user-agent="Mozilla/5.0" \\
            -O "$paper" "$url"; then
            fallback_url="${url/https:\\/\\/arxiv.org/https:\\/\\/export.arxiv.org}"
            wget -q \\
                --retry-connrefused \\
                --waitretry=2 \\
                --tries=5 \\
                --timeout=60 \\
                --user-agent="Mozilla/5.0" \\
                -O "$paper" "$fallback_url"
        fi
        test "$(head -c 4 "$paper")" = "%PDF"
"""
_ARXIV_PDF_URL_RE = re.compile(r"https://arxiv\.org/pdf/[0-9.]+v[0-9]+\.pdf")
_ARXIV_EXPECTED_PAPER_COUNT = 100
_TASK006_TOX_PREWARM = (
    "# VISTA task-006 verifier dependency prewarm.\n"
    "RUN set -eux; \\\n"
    '    export PATH="/opt/hostedtoolcache/Python/3.11.8/x64/bin:'
    "/opt/hostedtoolcache/Python/3.12.2/x64/bin:"
    "/opt/hostedtoolcache/Python/3.10.13/x64/bin:"
    "/opt/hostedtoolcache/Python/3.9.18/x64/bin:"
    "/opt/hostedtoolcache/Python/3.8.18/x64/bin:"
    '/opt/hostedtoolcache/Python/3.7.17/x64/bin:$PATH"; \\\n'
    "    python3.11 -m pip install --disable-pip-version-check 'tox==4.58.0'; \\\n"
    "    printf 'langchain==0.0.352\\nlangchain-core==0.1.23\\n' "
    "> /tmp/agentops-pip-constraints.txt; \\\n"
    "    export PIP_INDEX_URL=https://pypi.org/simple; \\\n"
    "    export PIP_CONSTRAINT=/tmp/agentops-pip-constraints.txt; \\\n"
    "    cd /home/github/build/failed/AgentOps-AI/agentops; \\\n"
    "    TOX_WORK_DIR=/tmp/vista-tox-work python3.11 -m tox run "
    "-e py38,py39,py310,py311,py312 --notest; \\\n"
    "    chmod -R a+rwX /tmp/vista-tox-work /root/.cache/pip\n"
)


class OpenCodeIdentityError(RuntimeError):
    """Base class for an image identity gate failure."""


class OpenCodeProbeError(OpenCodeIdentityError):
    """The container could not execute the identity probe."""


class OpenCodeIdentityMismatch(OpenCodeIdentityError):
    """The preinstalled OpenCode binary is not the frozen binary."""


class OpenCodeImageContractError(OpenCodeIdentityError):
    """The formal image registry is missing or not qualified."""


@dataclass(frozen=True, slots=True)
class OpenCodeIdentity:
    requested_version: str
    actual_version: str | None
    binary_digest: str | None
    command_path: str | None
    exit_code: int
    duration_ms: float
    timed_out: bool
    public_output: str


@dataclass(frozen=True, slots=True)
class TaskImageBuildContext:
    task_id: str
    stage_root: Path
    source_task_digest: str
    vendor_dockerfile_digest: str
    staged_dockerfile_digest: str
    build_context_digest: str
    build_cache_key: str
    opencode_version: str
    opencode_binary_digest: str


@dataclass(frozen=True, slots=True)
class TaskImageContract:
    task_id: str
    image_tag: str
    image_digest: str
    source_task_digest: str
    vendor_dockerfile_digest: str
    staged_dockerfile_digest: str
    build_context_digest: str
    opencode_version: str
    opencode_binary_digest: str
    stage_root: str

    @property
    def immutable_image_ref(self) -> str:
        return f"{self.image_tag}@{self.image_digest}"


@dataclass(frozen=True, slots=True)
class OpenCodeArtifact:
    version: str
    platform: str
    source_url: str
    binary_path: Path
    expected_binary_sha256: str
    actual_binary_sha256: str
    source_sha256: str
    reused: bool
    source_digest_kind: str = "binary_sha256"


def _return_code(result: Any) -> int:
    return int(getattr(result, "return_code", getattr(result, "exit_code", 1)))


def _timed_out(result: Any) -> bool:
    return bool(getattr(result, "timed_out", False))


def _probe_output(result: Any) -> str:
    stdout = str(getattr(result, "stdout", "") or "")
    stderr = str(getattr(result, "stderr", "") or "")
    return "\n".join(part for part in (stdout, stderr) if part)


def _parse_identity(
    output: str, *, requested_version: str, exit_code: int, duration_ms: float, timed_out: bool
) -> OpenCodeIdentity:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    command_path = next(
        (line for line in lines if line.startswith("/") and "opencode" in line), None
    )
    version_match = next(
        (match for line in lines for match in [_VERSION_RE.search(line)] if match), None
    )
    digest_match = next(
        (match for line in lines for match in [_DIGEST_RE.search(line)] if match), None
    )
    return OpenCodeIdentity(
        requested_version=requested_version,
        actual_version=version_match.group(1) if version_match else None,
        binary_digest=digest_match.group(1).lower() if digest_match else None,
        command_path=command_path,
        exit_code=exit_code,
        duration_ms=duration_ms,
        timed_out=timed_out,
        public_output=output,
    )


def _normalise_digest(value: str) -> str:
    return value.strip().lower().removeprefix("sha256:")


def validate_opencode_identity(
    identity: OpenCodeIdentity, *, expected_version: str, expected_digest: str
) -> OpenCodeIdentity:
    """Validate a probe result against the frozen image contract."""

    expected_digest = _normalise_digest(expected_digest)
    if identity.exit_code != 0 or identity.timed_out:
        raise OpenCodeProbeError(
            "OpenCode identity probe failed "
            f"(exit={identity.exit_code}, timed_out={identity.timed_out})"
        )
    if identity.actual_version != expected_version:
        raise OpenCodeIdentityMismatch(
            "OpenCode version mismatch: "
            f"expected {expected_version}, got {identity.actual_version or '<missing>'}"
        )
    if identity.binary_digest != expected_digest:
        raise OpenCodeIdentityMismatch(
            "OpenCode binary digest mismatch: "
            f"expected {expected_digest}, got {identity.binary_digest or '<missing>'}"
        )
    return identity


async def probe_opencode_identity(
    runtime: Any,
    *,
    workspace: str,
    expected_version: str = OPENCODE_VERSION,
    expected_digest: str,
    timeout_sec: float = 30,
) -> OpenCodeIdentity:
    """Probe and validate the image-installed OpenCode binary.

    This function is intentionally the only OpenCode setup action available to
    the runtime.  It performs no package installation and no network request.
    """

    started = time.perf_counter()
    result = await runtime.env.exec(
        OPENCODE_IDENTITY_PROBE_COMMAND,
        cwd=workspace,
        user="root",
        timeout_sec=timeout_sec,
    )
    duration_ms = (time.perf_counter() - started) * 1000
    identity = _parse_identity(
        _probe_output(result),
        requested_version=expected_version,
        exit_code=_return_code(result),
        duration_ms=duration_ms,
        timed_out=_timed_out(result),
    )
    return validate_opencode_identity(
        identity,
        expected_version=expected_version,
        expected_digest=expected_digest,
    )


def task_image_config_override(image_ref: str) -> dict[str, dict[str, str]]:
    """Return BenchFlow's supported overlay for a prebuilt task image."""

    image_ref = image_ref.strip()
    if not image_ref:
        raise ValueError("prebuilt task image reference must not be empty")
    return {"sandbox": {"docker_image": image_ref}}


def _required_digest(entry: dict[str, Any], key: str) -> str:
    value = str(entry.get(key, "")).strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        raise OpenCodeImageContractError(f"image registry field {key!r} is not a SHA-256 digest")
    return value


def load_task_image_contract(
    path: Path, task_id: str, *, project_root: Path | None = None
) -> TaskImageContract:
    """Load one qualified, digest-pinned task image from the frozen registry."""

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise OpenCodeImageContractError(f"cannot read image registry: {path}") from exc
    entry = raw.get("tasks", {}).get(task_id) if isinstance(raw, dict) else None
    if not isinstance(entry, dict):
        raise OpenCodeImageContractError(f"no image contract for {task_id}")
    if entry.get("status") != "qualified":
        raise OpenCodeImageContractError(
            f"{task_id} is not qualified; immutable image digest is unavailable"
        )
    image_tag = str(entry.get("image_tag", "")).strip()
    image_digest = str(entry.get("image_digest", "")).strip().lower()
    if not image_tag or "@" in image_tag:
        raise OpenCodeImageContractError(f"{task_id} has no valid immutable image tag")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_digest):
        raise OpenCodeImageContractError(
            f"{task_id} has no valid immutable image digest"
        )
    stage_root = str(entry.get("stage_root", "")).strip()
    if not stage_root:
        raise OpenCodeImageContractError(f"{task_id} has no immutable staged build context")
    if project_root is not None and not Path(stage_root).is_absolute():
        stage_root = str((project_root / stage_root).resolve())
    return TaskImageContract(
        task_id=task_id,
        image_tag=image_tag,
        image_digest=image_digest,
        source_task_digest=_required_digest(entry, "source_task_digest"),
        vendor_dockerfile_digest=_required_digest(entry, "vendor_dockerfile_digest"),
        staged_dockerfile_digest=_required_digest(entry, "staged_dockerfile_digest"),
        build_context_digest=_required_digest(entry, "build_context_digest"),
        opencode_version=str(entry.get("opencode_version", "")),
        opencode_binary_digest=_required_digest(entry, "opencode_binary_digest"),
        stage_root=stage_root,
    )


def _download_opencode_bytes(source_url: str) -> bytes:
    with urlopen(source_url, timeout=120) as response:  # noqa: S310 - URL is protocol input
        return response.read()


def _extract_binary(payload: bytes, archive_member: str | None) -> bytes:
    if archive_member is None:
        return payload
    if payload[:2] == b"PK":
        with zipfile.ZipFile(BytesIO(payload)) as archive:
            candidates = [
                name
                for name in archive.namelist()
                if not name.endswith("/")
                and (name == archive_member or Path(name).name == archive_member)
            ]
            if len(candidates) != 1:
                raise OpenCodeImageContractError(
                    f"OpenCode archive member {archive_member!r} was not uniquely found"
                )
            return archive.read(candidates[0])
    with tarfile.open(fileobj=BytesIO(payload), mode="r:gz") as archive:
        candidates = [
            member
            for member in archive.getmembers()
            if member.isfile()
            and (member.name == archive_member or Path(member.name).name == archive_member)
        ]
        if len(candidates) != 1:
            raise OpenCodeImageContractError(
                f"OpenCode archive member {archive_member!r} was not uniquely found"
            )
        stream = archive.extractfile(candidates[0])
        if stream is None:
            raise OpenCodeImageContractError(
                f"OpenCode archive member {archive_member!r} could not be read"
            )
        return stream.read()


def _prepare_opencode_artifact(
    *,
    artifact_root: Path,
    source_url: str,
    expected_binary_sha256: str | None,
    version: str,
    platform: str,
    archive_member: str | None,
    expected_source_sha256: str | None,
    source_digest_kind: str,
    downloader: Callable[[str], bytes] | None,
) -> OpenCodeArtifact:
    artifact_root = artifact_root.resolve()
    if expected_binary_sha256 is not None:
        expected_binary_sha256 = _normalise_digest(expected_binary_sha256)
        if not re.fullmatch(r"[0-9a-f]{64}", expected_binary_sha256):
            raise OpenCodeImageContractError("expected OpenCode binary digest is not SHA-256")
    if expected_source_sha256 is not None:
        expected_source_sha256 = _normalise_digest(expected_source_sha256)
        if not re.fullmatch(r"[0-9a-f]{64}", expected_source_sha256):
            raise OpenCodeImageContractError("expected OpenCode source digest is not SHA-256")
    if source_digest_kind not in {"archive_sha256", "binary_sha256"}:
        raise OpenCodeImageContractError(
            f"unsupported OpenCode source digest kind: {source_digest_kind}"
        )
    if expected_binary_sha256 is None and expected_source_sha256 is None:
        raise OpenCodeImageContractError(
            "expected OpenCode archive/source SHA-256 is required when binary SHA-256 is absent"
        )
    if not version or not platform:
        raise ValueError("OpenCode version and platform are required")
    artifact_root.mkdir(parents=True, exist_ok=True)
    binary_path = artifact_root / f"opencode-{version}-{platform}"
    manifest_path = artifact_root / f"opencode-{version}-{platform}.json"
    lock_path = artifact_root / f"opencode-{version}-{platform}.lock"
    if binary_path.exists():
        actual = sha256_file(binary_path)
        if expected_binary_sha256 is not None and actual != expected_binary_sha256:
            raise OpenCodeImageContractError(
                f"refusing to overwrite existing OpenCode artifact {binary_path}"
            )
        if not manifest_path.exists():
            if expected_binary_sha256 is None:
                raise OpenCodeImageContractError(
                    "refusing to reuse an OpenCode binary without a source-digest manifest"
                )
            source_sha256 = "unknown-existing-source"
            manifest_kind = "binary_sha256"
            return OpenCodeArtifact(
                version,
                platform,
                source_url,
                binary_path,
                expected_binary_sha256,
                actual,
                source_sha256,
                True,
                manifest_kind,
            )
        try:
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise OpenCodeImageContractError(
                f"existing OpenCode artifact manifest is unreadable: {manifest_path}"
            ) from exc
        existing_expected_binary = str(existing.get("expected_binary_sha256", "")).lower()
        existing_actual_binary = str(existing.get("actual_binary_sha256", "")).lower()
        if (
            existing.get("version") != version
            or existing_expected_binary != actual
            or existing_actual_binary != actual
        ):
            raise OpenCodeImageContractError(
                f"refusing to reuse mismatched OpenCode artifact manifest {manifest_path}"
            )
        source_sha256 = str(existing.get("source_sha256", ""))
        manifest_kind = str(existing.get("source_digest_kind", "binary_sha256"))
        if expected_source_sha256 is not None and (
            manifest_kind != source_digest_kind or source_sha256 != expected_source_sha256
        ):
            raise OpenCodeImageContractError(
                "refusing to reuse OpenCode artifact with mismatched source digest "
                f"manifest {manifest_path}"
            )
        if expected_binary_sha256 is not None and (
            existing_expected_binary != expected_binary_sha256
        ):
            raise OpenCodeImageContractError(
                f"refusing to reuse mismatched OpenCode artifact manifest {manifest_path}"
            )
        return OpenCodeArtifact(
            version,
            platform,
            source_url,
            binary_path,
            actual,
            actual,
            source_sha256,
            True,
            manifest_kind,
        )

    try:
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise OpenCodeImageContractError(
            f"OpenCode artifact preparation is already locked: {lock_path}"
        ) from exc
    try:
        os.close(lock_fd)
        payload = (downloader or _download_opencode_bytes)(source_url)
        source_sha256 = hashlib.sha256(payload).hexdigest()
        if expected_source_sha256 is not None and source_sha256 != expected_source_sha256:
            raise OpenCodeImageContractError(
                "OpenCode source digest mismatch: "
                f"expected {expected_source_sha256}, got {source_sha256}"
            )
        binary = _extract_binary(payload, archive_member)
        actual = hashlib.sha256(binary).hexdigest()
        if expected_binary_sha256 is not None and actual != expected_binary_sha256:
            raise OpenCodeImageContractError(
                f"OpenCode binary digest mismatch: expected {expected_binary_sha256}, got {actual}"
            )
        if expected_binary_sha256 is None:
            expected_binary_sha256 = actual
        temporary = artifact_root / f".{binary_path.name}.{uuid.uuid4().hex}.part"
        try:
            with temporary.open("xb") as stream:
                stream.write(binary)
                stream.flush()
                os.fsync(stream.fileno())
            if binary_path.exists():
                raise OpenCodeImageContractError(
                    f"refusing to overwrite concurrently-created OpenCode artifact {binary_path}"
                )
            os.replace(temporary, binary_path)
        finally:
            if temporary.exists():
                temporary.unlink()
        manifest = {
            "schema_version": 1,
            "version": version,
            "platform": platform,
            "source_url": source_url,
            "archive_member": archive_member,
            "source_digest_kind": source_digest_kind,
            "expected_source_sha256": expected_source_sha256,
            "source_sha256": source_sha256,
            "expected_binary_sha256": expected_binary_sha256,
            "actual_binary_sha256": actual,
            "binary_path": str(binary_path),
            "status": "verified",
        }
        manifest_temporary = artifact_root / f".{manifest_path.name}.{uuid.uuid4().hex}.part"
        try:
            with manifest_temporary.open("x", encoding="utf-8", newline="\n") as stream:
                json.dump(manifest, stream, indent=2, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(manifest_temporary, manifest_path)
        finally:
            if manifest_temporary.exists():
                manifest_temporary.unlink()
        return OpenCodeArtifact(
            version,
            platform,
            source_url,
            binary_path,
            expected_binary_sha256,
            actual,
            source_sha256,
            False,
            source_digest_kind,
        )
    finally:
        lock_path.unlink(missing_ok=True)


def prepare_opencode_artifact(
    *,
    artifact_root: Path,
    source_url: str,
    expected_binary_sha256: str,
    version: str = OPENCODE_VERSION,
    platform: str = "linux-x64",
    archive_member: str | None = None,
    expected_source_sha256: str | None = None,
    downloader: Callable[[str], bytes] | None = None,
) -> OpenCodeArtifact:
    """Prepare one binary when its extracted SHA-256 is already frozen."""

    return _prepare_opencode_artifact(
        artifact_root=artifact_root,
        source_url=source_url,
        expected_binary_sha256=expected_binary_sha256,
        version=version,
        platform=platform,
        archive_member=archive_member,
        expected_source_sha256=expected_source_sha256,
        source_digest_kind=("archive_sha256" if expected_source_sha256 else "binary_sha256"),
        downloader=downloader,
    )


def prepare_opencode_artifact_from_verified_source(
    *,
    artifact_root: Path,
    source_url: str,
    expected_source_sha256: str,
    version: str = OPENCODE_VERSION,
    platform: str = "linux-x64",
    archive_member: str | None = None,
    downloader: Callable[[str], bytes] | None = None,
) -> OpenCodeArtifact:
    """Verify a release archive, then derive and freeze the extracted binary SHA-256."""

    if not archive_member:
        raise OpenCodeImageContractError(
            "source-verified OpenCode preparation requires an archive member"
        )
    return _prepare_opencode_artifact(
        artifact_root=artifact_root,
        source_url=source_url,
        expected_binary_sha256=None,
        version=version,
        platform=platform,
        archive_member=archive_member,
        expected_source_sha256=expected_source_sha256,
        source_digest_kind="archive_sha256",
        downloader=downloader,
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_sha256(root: Path, *, ignored_relative_paths: frozenset[str] = frozenset()) -> str:
    """Hash files in an OS-independent relative-path order."""

    root = root.resolve()
    digest = hashlib.sha256()
    paths = [
        path
        for path in root.rglob("*")
        if path.is_file()
        and path.relative_to(root).as_posix() not in ignored_relative_paths
    ]
    paths.sort(
        key=lambda path: (
            path.relative_to(root).as_posix().casefold(),
            path.relative_to(root).as_posix(),
        )
    )
    for path in paths:
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        file_digest = bytes.fromhex(sha256_file(path))
        digest.update(file_digest)
    return digest.hexdigest()


def task_build_context_sha256(root: Path) -> str:
    """Hash immutable staged inputs while excluding post-build audit outputs."""

    return tree_sha256(
        root,
        ignored_relative_paths=frozenset({"docker-build.log", "image.iid"}),
    )


def _safe_component(value: str, *, field: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", value):
        raise ValueError(f"unsafe {field}: {value!r}")
    return value


def _add_apt_retry_policy(dockerfile: str) -> str:
    return re.sub(
        r"\bapt-get\s+(?=(?:update|install)\b)",
        f"apt-get {_APT_RETRY_OPTIONS} ",
        dockerfile,
    )


def _add_apt_mirror_policy(dockerfile: str) -> str:
    first_apt_run = re.search(r"(?m)^[ \t]*RUN[ \t]+[^\n]*\bapt-get\b", dockerfile)
    if first_apt_run is None:
        return dockerfile
    insertion = first_apt_run.start()
    return dockerfile[:insertion] + _APT_MIRROR_SETUP + dockerfile[insertion:]


def _replace_coursier_scala_install(
    dockerfile: str, *, install_sbt: bool = False
) -> str:
    lines = dockerfile.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if "coursier/releases/download/" in line and "./cs install scala:2.13.12" in line:
            lines[index] = _SCALA_INSTALL + (_SBT_INSTALL if install_sbt else "")
            break
    return "".join(lines)


def _verifier_requires_sbt(verifier_root: Path) -> bool:
    for path in verifier_root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {
            ".md",
            ".py",
            ".sh",
            ".txt",
            ".yaml",
            ".yml",
        }:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if re.search(r"\bsbt\b", text):
            return True
    return False


def _replace_verifier_coursier_setup(
    script: str, *, use_preinstalled_sbt: bool = False
) -> str:
    lines = script.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if "coursier/releases/download/" in line and "./cs setup --yes" in line:
            lines[index] = (
                "command -v sbt && sbt --script-version\n"
                if use_preinstalled_sbt
                else "scala -version && scalac -version\n"
            )
    return "".join(lines)


def _add_external_tox_work_dir(script: str) -> str:
    marker = "unset PYTEST_ADDOPTS\n"
    if "uv add pytest-json-ctrf" not in script or marker not in script:
        return script
    return script.replace(
        marker,
        marker + "export TOX_WORK_DIR=/tmp/vista-tox-work\n",
        1,
    )


def _add_task006_tox_prewarm(dockerfile: str, verifier_script: str) -> str:
    if (
        "bugswarm_image_tag=AgentOps-AI-agentops-24579293289" not in dockerfile
        or "export TOX_WORK_DIR=/tmp/vista-tox-work" not in verifier_script
    ):
        return dockerfile
    return dockerfile.rstrip() + "\n\n" + _TASK006_TOX_PREWARM


def _add_sbt_prewarm(dockerfile: str, *, verifier_requires_sbt: bool) -> str:
    if not verifier_requires_sbt:
        return dockerfile
    return dockerfile.rstrip() + (
        "\n\n# VISTA task verifier SBT dependency prewarm.\n"
        "RUN cd /root/localtest && sbt -batch update\n"
    )


def _add_arxiv_download_fallback(text: str) -> str:
    return text.replace(_ARXIV_WGET_BLOCK, _ARXIV_WGET_WITH_FALLBACK)


def _materialize_arxiv_papers(environment_root: Path) -> None:
    download_script = environment_root / "download_papers.sh"
    if not download_script.is_file():
        return
    script = download_script.read_text(encoding="utf-8")
    urls = list(dict.fromkeys(_ARXIV_PDF_URL_RE.findall(script)))
    if not urls:
        return
    if len(urls) != _ARXIV_EXPECTED_PAPER_COUNT:
        raise OpenCodeImageContractError(
            "formal arXiv corpus must contain exactly "
            f"{_ARXIV_EXPECTED_PAPER_COUNT} unique URLs; found {len(urls)}"
        )

    paper_root = environment_root / "_vista_papers"
    paper_root.mkdir()
    for url in urls:
        target = paper_root / url.rsplit("/", 1)[-1]
        temporary = target.with_suffix(target.suffix + ".part")
        candidates = (url, url.replace("https://arxiv.org/", "https://export.arxiv.org/"))
        last_error: Exception | None = None
        for candidate in candidates:
            for attempt in range(1, 4):
                try:
                    request = Request(candidate, headers={"User-Agent": "Mozilla/5.0"})
                    with urlopen(request, timeout=120) as response:  # noqa: S310
                        payload = response.read()
                    if not payload.startswith(b"%PDF"):
                        raise OpenCodeImageContractError(
                            f"downloaded arXiv artifact is not a PDF: {target.name}"
                        )
                    temporary.write_bytes(payload)
                    temporary.replace(target)
                    last_error = None
                    break
                except Exception as exc:
                    last_error = exc
                    if attempt < 3:
                        time.sleep(attempt)
            if last_error is None:
                break
        if last_error is not None:
            raise OpenCodeImageContractError(
                f"failed to materialize arXiv artifact {target.name}: {last_error}"
            ) from last_error

    dockerfile = environment_root / "Dockerfile"
    text = dockerfile.read_text(encoding="utf-8")
    text, replaced = re.subn(
        r"(?ms)^RUN <<'EOF'\n.*?^EOF\n",
        "COPY _vista_papers /root/papers/all\n",
        text,
        count=1,
    )
    if replaced != 1:
        raise OpenCodeImageContractError(
            "formal arXiv corpus was materialized but its Dockerfile download block was not found"
        )
    text = text.replace(
        "COPY download_papers.sh /tmp/download_papers.sh\n"
        "RUN bash /tmp/download_papers.sh && rm /tmp/download_papers.sh\n",
        "",
    )
    dockerfile.write_text(text, encoding="utf-8", newline="\n")


def stage_task_image_context(
    *,
    source_task: Path,
    project_root: Path,
    task_id: str,
    build_id: str,
    artifact_path: Path,
    opencode_version: str = OPENCODE_VERSION,
    opencode_digest: str,
    prepare_external_artifacts: bool = False,
) -> TaskImageBuildContext:
    """Create a task-scoped build context with the verified binary copied once.

    The context contains the same public ``task.md``, ``environment`` and
    ``verifier`` inputs used by the BenchFlow adapter.  It deliberately omits
    oracle material and never writes to the vendor checkout.  A unique build id
    makes an existing context immutable evidence instead of a cache directory
    that can be silently overwritten.
    """

    source_task = source_task.resolve()
    project_root = project_root.resolve()
    if not source_task.is_dir():
        raise FileNotFoundError(f"source task does not exist: {source_task}")
    if not (source_task / "task.md").is_file():
        raise FileNotFoundError(f"source task is missing task.md: {source_task}")
    for name in ("environment", "verifier"):
        if not (source_task / name).is_dir():
            raise FileNotFoundError(f"source task is missing public {name}: {source_task}")
    artifact_path = artifact_path.resolve()
    if not artifact_path.is_file():
        raise FileNotFoundError(f"OpenCode artifact does not exist: {artifact_path}")
    normalized_digest = _normalise_digest(opencode_digest)
    actual_digest = sha256_file(artifact_path)
    if actual_digest != normalized_digest:
        raise ValueError(
            "OpenCode artifact digest mismatch: "
            f"expected {normalized_digest}, got {actual_digest}"
        )
    _safe_component(task_id, field="task_id")
    _safe_component(build_id, field="build_id")
    stage_root = (
        project_root / "data" / "raw" / "_opencode_builds" / build_id / "tasks" / task_id
    )
    if stage_root.exists():
        raise FileExistsError(
            f"refusing to overwrite immutable task build context: {stage_root}"
        )
    stage_root.parent.mkdir(parents=True, exist_ok=True)
    stage_root.mkdir()

    source_dockerfile = source_task / "environment" / "Dockerfile"
    vendor_dockerfile_digest = sha256_file(source_dockerfile)
    source_task_digest = tree_sha256(source_task)
    shutil.copy2(source_task / "task.md", stage_root / "task.md")
    shutil.copytree(source_task / "environment", stage_root / "environment")
    shutil.copytree(source_task / "verifier", stage_root / "verifier")
    verifier_requires_sbt = _verifier_requires_sbt(stage_root / "verifier")

    text_suffixes = {
        ".md",
        ".py",
        ".sh",
        ".txt",
        ".yaml",
        ".yml",
    }
    for text_root in (stage_root / "environment", stage_root / "verifier"):
        for path in text_root.rglob("*"):
            if path.is_file() and path.suffix.lower() in text_suffixes:
                data = path.read_bytes()
                normalized = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
                if text_root == stage_root / "environment":
                    normalized = _add_arxiv_download_fallback(
                        normalized.decode("utf-8")
                    ).encode("utf-8")
                if text_root == stage_root / "verifier" and path.suffix.lower() == ".sh":
                    normalized_text = _replace_verifier_coursier_setup(
                        normalized.decode("utf-8"),
                        use_preinstalled_sbt=verifier_requires_sbt,
                    )
                    normalized = _add_external_tox_work_dir(normalized_text).encode(
                        "utf-8"
                    )
                if data != normalized:
                    path.write_bytes(normalized)

    verifier_root = stage_root / "verifier"
    for path in verifier_root.rglob("*"):
        if path.is_file() and path.suffix.lower() in {
            ".md",
            ".py",
            ".sh",
            ".txt",
            ".yaml",
            ".yml",
        }:
            # Keep the verifier traversal explicit as an audit assertion: all
            # copied verifier text has already been normalized above.
            if b"\r" in path.read_bytes():
                raise OpenCodeImageContractError(
                    f"staged verifier contains CR line endings: {path}"
                )

    if prepare_external_artifacts:
        _materialize_arxiv_papers(stage_root / "environment")

    tool_dir = stage_root / "environment" / "_vista_tool_layer"
    tool_dir.mkdir()
    shutil.copy2(artifact_path, tool_dir / "opencode")
    dockerfile = stage_root / "environment" / "Dockerfile"
    staged_dockerfile = dockerfile.read_text(encoding="utf-8")
    staged_dockerfile = _add_arxiv_download_fallback(staged_dockerfile)
    staged_dockerfile = _add_apt_mirror_policy(staged_dockerfile)
    staged_dockerfile = _replace_coursier_scala_install(
        staged_dockerfile, install_sbt=verifier_requires_sbt
    )
    staged_dockerfile = _add_apt_retry_policy(staged_dockerfile)
    verifier_path = stage_root / "verifier" / "test.sh"
    verifier_script = (
        verifier_path.read_text(encoding="utf-8") if verifier_path.is_file() else ""
    )
    staged_dockerfile = _add_task006_tox_prewarm(staged_dockerfile, verifier_script)
    staged_dockerfile = _add_sbt_prewarm(
        staged_dockerfile, verifier_requires_sbt=verifier_requires_sbt
    )
    dockerfile.write_text(
        staged_dockerfile.rstrip() + "\n\n"
        "# VISTA OpenCode tool layer: prepared and verified before task image build.\n"
        f"# version={opencode_version} sha256={normalized_digest}\n"
        "COPY _vista_tool_layer/opencode /usr/local/bin/opencode\n"
        "RUN chmod 0755 /usr/local/bin/opencode && "
        f"test \"$(/usr/local/bin/opencode --version)\" = \"{opencode_version}\" && "
        f"printf '%s  /usr/local/bin/opencode\\n' '{normalized_digest}' | sha256sum -c -\n",
        encoding="utf-8",
        newline="\n",
    )
    staged_dockerfile_digest = sha256_file(dockerfile)
    build_context_digest = task_build_context_sha256(stage_root)
    cache_material = "\n".join(
        (
            task_id,
            source_task_digest,
            vendor_dockerfile_digest,
            staged_dockerfile_digest,
            opencode_version,
            normalized_digest,
        )
    ).encode("utf-8")
    build_cache_key = hashlib.sha256(cache_material).hexdigest()
    return TaskImageBuildContext(
        task_id=task_id,
        stage_root=stage_root,
        source_task_digest=source_task_digest,
        vendor_dockerfile_digest=vendor_dockerfile_digest,
        staged_dockerfile_digest=staged_dockerfile_digest,
        build_context_digest=build_context_digest,
        build_cache_key=build_cache_key,
        opencode_version=opencode_version,
        opencode_binary_digest=normalized_digest,
    )


__all__ = [
    "OPENCODE_BINARY_PATH",
    "OPENCODE_IDENTITY_PROBE_COMMAND",
    "OPENCODE_VERSION",
    "OpenCodeIdentity",
    "OpenCodeIdentityError",
    "OpenCodeIdentityMismatch",
    "OpenCodeImageContractError",
    "OpenCodeProbeError",
    "OpenCodeArtifact",
    "TaskImageContract",
    "TaskImageBuildContext",
    "load_task_image_contract",
    "prepare_opencode_artifact",
    "prepare_opencode_artifact_from_verified_source",
    "probe_opencode_identity",
    "sha256_file",
    "stage_task_image_context",
    "task_build_context_sha256",
    "task_image_config_override",
    "tree_sha256",
    "validate_opencode_identity",
]
