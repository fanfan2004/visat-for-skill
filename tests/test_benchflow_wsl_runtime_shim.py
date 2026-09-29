from pathlib import Path

ROOT = Path(__file__).parents[1]
BRIDGE = ROOT / "experiments" / "benchflow" / "smoke.sh"
SHIM = ROOT / "experiments" / "benchflow" / "runtime_shim" / "sitecustomize.py"
PROXY_COMPOSE = ROOT / "experiments" / "benchflow" / "runtime_shim" / "docker-compose-proxy.yaml"


def test_wsl_runtime_shim_is_loaded_and_advertises_docker_desktop_host() -> None:
    bridge_text = BRIDGE.read_text(encoding="utf-8")
    shim_text = SHIM.read_text(encoding="utf-8")

    assert "runtime_shim" in bridge_text
    assert "PYTHONPATH" in bridge_text
    assert '"$REPO_ROOT/.tools/benchflow/bin/bench"' in bridge_text
    assert "/usr/bin/env -u LLM_MODEL" in bridge_text
    assert "_docker_host_address" in shim_text
    assert "host.docker.internal" in shim_text


def test_wsl_runtime_shim_can_forward_proxy_into_task_container() -> None:
    bridge_text = BRIDGE.read_text(encoding="utf-8")
    shim_text = SHIM.read_text(encoding="utf-8")
    compose_text = PROXY_COMPOSE.read_text(encoding="utf-8")

    assert "VISTA_DOCKER_PROXY_COMPOSE" in bridge_text
    assert "DockerSandbox._docker_compose_paths" in shim_text
    assert "VISTA_DOCKER_HTTP_PROXY" in compose_text
    assert "VISTA_DOCKER_HTTPS_PROXY" in compose_text
