"""Run the 3-condition x 3-replicate task-007 travel-planning experiment."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from vista.feedback import FeedbackObservation, FeedbackParser, FeedbackSource
from vista.runtime import DisclosureRouter, SkillManifest
from vista.runtime.opencode_local_backend import command_for_turn, freeze_launcher


REPO = Path(os.environ.get("VISTA_REPO_ROOT", Path(__file__).resolve().parents[2])).resolve()
TASK_SOURCE = REPO / "vendor" / "skillsbench" / "tasks" / "travel-planning"
VISTA_SOURCE = REPO / "skills" / "pd-vista" / "task-007-travel-planning"
RUN_PREFIX = os.environ.get(
    "VISTA_RUN_PREFIX",
    f"formal-task007-three-condition-opencode-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
)
PROTOCOL = os.environ.get(
    "VISTA_PROTOCOL",
    "task007-opencode-current-config-noskill-native-pd-vista-3x3",
)
if os.environ.get("VISTA_EXPERIMENT_ROOT"):
    LOCAL_ROOT = Path(os.environ["VISTA_EXPERIMENT_ROOT"]).expanduser().resolve()
elif os.name == "nt":
    LOCAL_ROOT = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "VistaExperiments"
else:
    LOCAL_ROOT = Path.home() / "Library" / "Caches" / "VistaExperiments"
RESULT_ROOT = REPO / "data" / "raw" / RUN_PREFIX
# Do not resolve this path: a virtualenv interpreter is normally a symlink to the base Python,
# and resolving it would bypass the virtualenv's installed verifier dependencies.
VERIFIER_PYTHON = Path(
    os.environ.get("VISTA_VERIFIER_PYTHON", sys.executable)
).expanduser().absolute()
MAX_TURNS = 6
CONDITIONS = ("noskill", "native", "pd-vista")
REPLICATES = (1, 2, 3)
# A balanced cyclic order prevents a condition from always receiving the earliest provider slot.
SCHEDULE = (
    (1, "noskill"), (1, "native"), (1, "pd-vista"),
    (2, "native"), (2, "pd-vista"), (2, "noskill"),
    (3, "pd-vista"), (3, "noskill"), (3, "native"),
)
MODULE_IDS = (
    "search-cities",
    "search-accommodations",
    "search-restaurants",
    "search-attractions",
    "search-driving-distance",
    "search-flights",
)

HOST_TOOLING = "PowerShell or Python" if os.name == "nt" else "the shell or Python"
COMMON_PROMPT = f"""Solve the isolated SkillsBench task in this working directory. Work only inside this directory and do not access parent or external directories. First read task.md. On this host, /app/data maps to ./data and /app/output/itinerary.json maps to ./output/itinerary.json. Produce the requested itinerary.json using only local database evidence. Use {HOST_TOOLING}; do not use unavailable tools. Respect every stated constraint, especially the no-flight requirement."""
INITIAL_PROMPTS = {
    "noskill": COMMON_PROMPT + " No task skill is available in this condition; solve from task.md and the local data only.",
    "native": COMMON_PROMPT + " All original task skills are available under ./skills/<module-id>/SKILL.md. Read and use the relevant skills and bundled scripts.",
    "pd-vista": COMMON_PROMPT + " Read VISTA.md. No original search module is initially disclosed; after a public verifier failure VISTA may disclose one relevant module under ./skills/<module-id>/SKILL.md. Read any disclosed module and use its bundled scripts.",
}


def current_opencode_config() -> tuple[Path, str, str | None]:
    """Resolve the current config, default model, and optional referenced key variable.

    OpenCode built-in providers can be authenticated through its auth store and therefore do
    not need a matching custom ``provider`` block in opencode.json.
    """

    path = Path(
        os.environ.get(
            "VISTA_OPENCODE_CONFIG",
            Path.home() / ".config" / "opencode" / "opencode.json",
        )
    ).expanduser().resolve()
    if not path.is_file():
        raise RuntimeError(f"OpenCode config is missing: {path}")
    config = json.loads(path.read_text(encoding="utf-8"))
    if config.get("share") != "disabled":
        raise RuntimeError("OpenCode sharing must be disabled for the experiment")
    permissions = config.get("permission") or {}
    if permissions.get("external_directory") != "deny":
        raise RuntimeError("OpenCode external_directory permission must be deny")
    model = str(config.get("model") or "")
    if "/" not in model:
        raise RuntimeError("OpenCode config must declare a provider/model default")
    provider_id, model_id = model.split("/", 1)
    provider = (config.get("provider") or {}).get(provider_id)
    if provider is None:
        return path, model, None
    if not isinstance(provider, dict) or model_id not in (provider.get("models") or {}):
        raise RuntimeError(f"OpenCode default model is not declared by its custom provider: {model}")
    api_key = str((provider.get("options") or {}).get("apiKey") or "")
    match = re.fullmatch(r"\{env:([^}]+)\}", api_key)
    if not match:
        raise RuntimeError("the selected OpenCode provider must reference its API key via {env:NAME}")
    return path, model, match.group(1)


def verifier_infrastructure_failure(output: str) -> bool:
    folded = output.casefold()
    return any(marker in folded for marker in (
        "no module named", "modulenotfounderror", "failed to import",
        "error while loading conftest", "unrecognized arguments", "unicodedecodeerror",
    ))


def parse_events(text: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in text.splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            events.append(value)
    return events


def nested_values(value: Any, key: str) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for name, child in value.items():
            if name == key and child:
                found.append(str(child))
            found.extend(nested_values(child, key))
    elif isinstance(value, list):
        for child in value:
            found.extend(nested_values(child, key))
    return found


def event_summary(
    events: list[dict[str, Any]], module_ids: tuple[str, ...] = MODULE_IDS
) -> dict[str, Any]:
    sessions: list[str] = []
    models: list[str] = []
    providers: list[str] = []
    requests = tool_uses = input_tokens = output_tokens = cache_read_tokens = 0
    accessed_modules: list[str] = []
    errors: list[dict[str, Any]] = []
    tool_errors: list[str] = []
    types: dict[str, int] = {}
    for event in events:
        kind = str(event.get("type") or "")
        types[kind] = types.get(kind, 0) + 1
        sessions.extend(nested_values(event, "sessionID"))
        models.extend(nested_values(event, "modelID"))
        providers.extend(nested_values(event, "providerID"))
        part = event.get("part") if isinstance(event.get("part"), dict) else {}
        if kind == "step_start":
            requests += 1
        if kind == "step_finish":
            tokens = part.get("tokens") if isinstance(part.get("tokens"), dict) else {}
            input_tokens += int(tokens.get("input") or 0)
            output_tokens += int(tokens.get("output") or 0)
            cache = tokens.get("cache") if isinstance(tokens.get("cache"), dict) else {}
            cache_read_tokens += int(cache.get("read") or 0)
        if kind == "tool_use":
            tool_uses += 1
            state = part.get("state") if isinstance(part.get("state"), dict) else {}
            rendered = json.dumps(event, ensure_ascii=False).casefold()
            if state.get("status") == "completed" and "skill.md" in rendered:
                for module_id in module_ids:
                    if module_id in rendered:
                        accessed_modules.append(module_id)
            if state.get("status") == "error":
                tool_errors.append(str(state.get("error") or "tool_error")[:500])
        if kind in {"error", "fatal"}:
            raw = event.get("error") if isinstance(event.get("error"), dict) else {}
            data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
            errors.append({
                "name": str(raw.get("name") or "error"),
                "status_code": data.get("statusCode"),
                "retryable": data.get("isRetryable"),
                "category": "provider_policy_rejection" if data.get("statusCode") == 403 else "api_error",
            })
    return {
        "event_types": types,
        "session_ids": list(dict.fromkeys(sessions)),
        "actual_model_ids": list(dict.fromkeys(models)),
        "provider_ids": list(dict.fromkeys(providers)),
        "requests": requests,
        "tool_uses": tool_uses,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read_tokens,
        "accessed_modules": list(dict.fromkeys(accessed_modules)),
        "errors": errors,
        "tool_errors": tool_errors,
    }


def prepare_trial(condition: str, replicate: int) -> tuple[Path, Path, Path, Path]:
    if condition not in CONDITIONS:
        raise ValueError(f"unsupported condition: {condition}")
    run_id = f"{RUN_PREFIX}-{condition}-replicate-{replicate:02d}"
    base = LOCAL_ROOT / run_id
    if base.exists() and any(base.glob("turn-*.jsonl")):
        raise RuntimeError(f"refusing to reuse a trial that has model-turn evidence: {base}")
    workspace = base / "workspace"
    verifier = base / "verifier-run"
    router_source = base / "router-source"
    workspace.mkdir(parents=True, exist_ok=True)
    verifier.mkdir(exist_ok=True)
    router_source.mkdir(exist_ok=True)
    (workspace / "output").mkdir(exist_ok=True)
    skills_root = workspace / "skills"
    skills_root.mkdir(exist_ok=True)
    if not (workspace / "data").is_dir():
        shutil.copytree(TASK_SOURCE / "environment" / "data", workspace / "data")
    data_link = skills_root / "data"
    if condition != "noskill" and not data_link.is_dir():
        if os.name == "nt":
            junction = subprocess.run(
                ["cmd.exe", "/d", "/c", "mklink", "/J", str(data_link), str(workspace / "data")],
                capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
            )
            if junction.returncode != 0:
                raise RuntimeError("could not create the per-trial Windows data junction")
        else:
            data_link.symlink_to(workspace / "data", target_is_directory=True)
        if not data_link.is_dir():
            raise RuntimeError("could not create the per-trial data link for original skill scripts")
    shutil.copy2(TASK_SOURCE / "task.md", workspace / "task.md")
    if condition == "pd-vista":
        shutil.copy2(VISTA_SOURCE / "SKILL.md", workspace / "VISTA.md")
    elif condition == "native":
        for module_id in MODULE_IDS:
            destination = skills_root / module_id
            if not destination.is_dir():
                shutil.copytree(TASK_SOURCE / "environment" / "skills" / module_id, destination)
    verifier_text = (TASK_SOURCE / "verifier" / "test_outputs.py").read_text(encoding="utf-8")
    verifier_text = verifier_text.replace(
        'PLAN_PATH = "/app/output/itinerary.json"',
        f'PLAN_PATH = r"{workspace / "output" / "itinerary.json"}"',
    ).replace(
        'Path("/app/data/restaurants/clean_restaurant_2022.csv")',
        f'Path(r"{workspace / "data" / "restaurants" / "clean_restaurant_2022.csv"}")',
    )
    (verifier / "test_outputs.py").write_text(verifier_text, encoding="utf-8")
    if condition == "pd-vista":
        shutil.copy2(VISTA_SOURCE / "manifest.yaml", router_source / "manifest.yaml")
        for module_id in MODULE_IDS:
            destination = router_source / "modules" / module_id
            if not destination.is_dir():
                shutil.copytree(TASK_SOURCE / "environment" / "skills" / module_id, destination)
    return base, workspace, verifier, router_source


def verifier_readiness() -> tuple[bool, str]:
    if not VERIFIER_PYTHON.is_file():
        return False, f"verifier Python missing: {VERIFIER_PYTHON}"
    command = [
        str(VERIFIER_PYTHON), "-m", "pytest", "--collect-only", "-q",
        str(TASK_SOURCE / "verifier" / "test_outputs.py"),
        f"--confcutdir={TASK_SOURCE / 'verifier'}",
    ]
    verifier_env = os.environ.copy()
    verifier_env["PYTHONUTF8"] = "1"
    verifier_env["PYTHONIOENCODING"] = "utf-8"
    completed = subprocess.run(command, cwd=TASK_SOURCE / "verifier", env=verifier_env, capture_output=True,
                               text=True, encoding="utf-8", errors="replace", timeout=120,
                               check=False)
    output = (completed.stdout or "") + (completed.stderr or "")
    ok = completed.returncode == 0 and "10 tests collected" in output and not verifier_infrastructure_failure(output)
    return ok, output


def run_verifier(verifier: Path, turn: int) -> tuple[bool, str, bool]:
    temp = verifier / f"pytest-temp-turn-{turn}"
    command = [str(VERIFIER_PYTHON), "-m", "pytest", "-q",
               str(verifier / "test_outputs.py"), f"--confcutdir={verifier}",
               f"--basetemp={temp}"]
    verifier_env = os.environ.copy()
    verifier_env["PYTHONUTF8"] = "1"
    verifier_env["PYTHONIOENCODING"] = "utf-8"
    completed = subprocess.run(command, cwd=verifier, env=verifier_env, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=300, check=False)
    output = (completed.stdout or "") + (completed.stderr or "")
    (verifier / f"verifier-turn-{turn}.txt").write_text(output, encoding="utf-8")
    infra = verifier_infrastructure_failure(output)
    return completed.returncode == 0 and not infra, output, infra


def public_feedback(plan_exists: bool, output: str, tool_errors: list[str]) -> tuple[list[str], str]:
    kinds: list[str] = []
    notes: list[str] = []
    if not plan_exists or "Plan JSON not found" in output:
        kinds.append("incomplete_output")
        notes.append("itinerary.json is missing")
    if any(marker in output for marker in (
        "Top-level JSON should be an object", "Top-level 'plan' key missing",
        "'plan' should be a list", "Expected 7 days", "entry must be an object",
        "missing fields", "Day numbering should be sequential",
    )):
        kinds.extend(["schema_error", "wrong_format"])
        notes.append("the seven-day JSON structure or required fields are invalid")
    if "transportation should not include flights" in output:
        kinds.append("transportation_constraint")
        notes.append("the no-flight transportation constraint is violated")
    if any(marker in output for marker in (
        "Day 1 should start from Minneapolis", "Expected at least 3 distinct cities",
        "Expected to visit Ohio cities",
    )):
        kinds.append("geographic_constraint")
        notes.append("the origin, Ohio destination, or three-city constraint is unmet")
    if "At least one accommodation should indicate pet-friendly" in output:
        kinds.append("accommodation_constraint")
        notes.append("the pet-friendly accommodation constraint is unmet")
    if "Expected at least 3 of the 4 preferred cuisines" in output:
        kinds.append("cuisine_constraint")
        notes.append("fewer than three requested cuisine categories are evidenced")
    if "attraction should not be empty" in output:
        kinds.append("attraction_constraint")
        notes.append("one or more daily attractions are empty")
    if tool_errors:
        kinds.append("runtime_error")
        notes.append("an agent-visible local tool call failed; use an available Windows alternative")
    if not kinds:
        kinds.append("test_failure")
        notes.append("the public verifier still fails; inspect local evidence and repair the itinerary")
    kinds = list(dict.fromkeys(kinds))
    if "test_failure" not in kinds:
        kinds.append("test_failure")
    return kinds, "; ".join(dict.fromkeys(notes))


def copy_disclosed_module(router_source: Path, workspace: Path, module_id: str) -> Path:
    source = router_source / "modules" / module_id
    destination = workspace / "skills" / module_id
    if destination.exists():
        raise RuntimeError(f"refusing to overwrite disclosed module: {destination}")
    shutil.copytree(source, destination)
    return destination / "SKILL.md"


def link_opencode_auth_store(base: Path) -> None:
    """Expose only OpenCode credentials to an otherwise isolated per-trial data home."""

    source = Path.home() / ".local" / "share" / "opencode" / "auth.json"
    if not source.is_file():
        return
    destination = base / "opencode-state" / "data" / "opencode" / "auth.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() or destination.is_symlink():
        if destination.is_symlink() and destination.resolve() == source.resolve():
            return
        raise RuntimeError(f"refusing to replace an unexpected OpenCode auth target: {destination}")
    destination.symlink_to(source)


def run_replicate(
    condition: str,
    replicate: int,
    *,
    config_path: Path,
    requested_model: str,
) -> dict[str, Any]:
    base, workspace, verifier, router_source = prepare_trial(condition, replicate)
    parser = FeedbackParser() if condition == "pd-vista" else None
    router = (
        DisclosureRouter(SkillManifest.load(router_source / "manifest.yaml"))
        if condition == "pd-vista"
        else None
    )
    disclosed: list[str] = []
    accessed_after_disclosure: list[str] = []
    session_id: str | None = None
    prompt = INITIAL_PROMPTS[condition]
    turns: list[dict[str, Any]] = []
    trigger_opportunities = routable_feedback = disclosure_count = access_rounds = 0
    final_pass = False
    stop_reason = "max_turns"
    process_env = os.environ.copy()
    link_opencode_auth_store(base)
    process_env["OPENCODE_CONFIG"] = str(config_path)
    process_env.pop("OPENCODE_CONFIG_DIR", None)
    process_env["XDG_CONFIG_HOME"] = str(base / "opencode-state" / "config")
    process_env["XDG_DATA_HOME"] = str(base / "opencode-state" / "data")
    process_env["XDG_CACHE_HOME"] = str(base / "opencode-state" / "cache")

    for turn in range(1, MAX_TURNS + 1):
        transport_attempts: list[dict[str, Any]] = []
        for transport_attempt in (1, 2):
            completed = subprocess.run(
                command_for_turn(
                    workspace=workspace,
                    prompt=prompt,
                    session_id=session_id,
                    model=requested_model,
                ),
                cwd=workspace, env=process_env, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=1800, check=False,
            )
            attempt_summary = event_summary(parse_events(completed.stdout or ""))
            (base / f"turn-{turn}-attempt-{transport_attempt}.jsonl").write_text(
                completed.stdout or "", encoding="utf-8"
            )
            (base / f"turn-{turn}-attempt-{transport_attempt}.stderr.txt").write_text(
                completed.stderr or "", encoding="utf-8"
            )
            if attempt_summary["session_ids"]:
                observed = attempt_summary["session_ids"][0]
                if session_id is not None and observed != session_id:
                    stop_reason = "session_identity_changed"
                    break
                session_id = observed
            retryable_transport = bool(attempt_summary["errors"]) and all(
                error.get("retryable") is True for error in attempt_summary["errors"]
            ) and attempt_summary["requests"] == 0
            transport_attempts.append({
                "attempt": transport_attempt,
                "return_code": completed.returncode,
                "retryable_transport_error": retryable_transport,
            })
            if not retryable_transport or transport_attempt == 2:
                break
        (base / f"turn-{turn}.jsonl").write_text(completed.stdout or "", encoding="utf-8")
        (base / f"turn-{turn}.stderr.txt").write_text(completed.stderr or "", encoding="utf-8")
        summary = event_summary(parse_events(completed.stdout or ""))
        if summary["session_ids"]:
            observed = summary["session_ids"][0]
            if session_id is not None and observed != session_id:
                stop_reason = "session_identity_changed"
                break
            session_id = observed
        accessed_now = [item for item in summary["accessed_modules"] if item in disclosed]
        if accessed_now:
            access_rounds += 1
            accessed_after_disclosure.extend(accessed_now)
        plan_exists = (workspace / "output" / "itinerary.json").is_file()
        verifier_pass, verifier_output, verifier_infra = run_verifier(verifier, turn)
        turns.append({
            "turn": turn, "return_code": completed.returncode,
            "transport_attempts": transport_attempts,
            "event_summary": summary, "plan_exists": plan_exists,
            "verifier_pass": verifier_pass, "verifier_infrastructure_error": verifier_infra,
        })
        print(f"condition={condition} replicate={replicate} turn={turn} return={completed.returncode} "
              f"requests={summary['requests']} tools={summary['tool_uses']} "
              f"accessed={accessed_now} verifier_pass={verifier_pass}", flush=True)
        if verifier_pass:
            final_pass = True
            stop_reason = "verifier_pass"
            break
        if verifier_infra:
            stop_reason = "verifier_infrastructure_error"
            break
        if summary["errors"]:
            stop_reason = summary["errors"][-1]["category"]
            break

        trigger_opportunities += 1
        feedback_types, safe_summary = public_feedback(plan_exists, verifier_output, summary["tool_errors"])
        payload = json.dumps({
            "schema_version": 1, "kind": "vista_public_verifier_feedback",
            "outcome": "fail", "feedback_types": feedback_types, "summary": safe_summary,
        }, ensure_ascii=False)
        newly_disclosed: list[str] = []
        if condition == "pd-vista":
            assert parser is not None and router is not None
            classification = parser.parse(
                FeedbackObservation(source=FeedbackSource.TEST_OUTPUT, text=payload, exit_code=1),
                trial_id=f"{RUN_PREFIX}-{condition}-replicate-{replicate:02d}",
            )
            decision = router.route(classification, previous_modules=disclosed)
            if decision.modules or decision.skipped_previous_modules:
                routable_feedback += 1
            for module in router.disclose(decision):
                copy_disclosed_module(router_source, workspace, module.module_id)
                disclosed.append(module.module_id)
                newly_disclosed.append(module.module_id)
                disclosure_count += 1
        if condition == "pd-vista" and newly_disclosed:
            instruction = "VISTA disclosed " + ", ".join(
                f"./skills/{item}/SKILL.md" for item in newly_disclosed
            ) + ". Read it before repairing."
        elif condition == "pd-vista" and disclosed:
            instruction = "Use the already disclosed modules: " + ", ".join(disclosed) + "."
        elif condition == "native":
            instruction = "Use the original modules already available under ./skills/."
        else:
            instruction = "No task skill is available; repair from the public diagnostic and local data."
        prompt = (
            f"A public check failed with {', '.join(feedback_types)}. Public diagnostic: "
            f"{safe_summary}. {instruction} Continue in the same session and work only inside "
            "this directory. Use the condition's available local resources as needed, preserve "
            "the no-flight constraint, and repair ./output/itinerary.json."
        )

    result_dir = RESULT_ROOT / condition / f"replicate-{replicate:02d}"
    result_dir.mkdir(parents=True)
    for artifact in base.glob("turn-*.*"):
        shutil.copy2(artifact, result_dir / artifact.name)
    for artifact in verifier.glob("verifier-turn-*.txt"):
        shutil.copy2(artifact, result_dir / artifact.name)
    if (workspace / "output" / "itinerary.json").is_file():
        shutil.copy2(workspace / "output" / "itinerary.json", result_dir / "itinerary.json")
    actual_models = list(dict.fromkeys(
        model for turn in turns for model in turn["event_summary"]["actual_model_ids"]
    ))
    actual_providers = list(dict.fromkeys(
        provider for turn in turns for provider in turn["event_summary"]["provider_ids"]
    ))
    expected_provider, expected_model = requested_model.split("/", 1)
    if actual_models and set(actual_models) != {expected_model}:
        stop_reason = "model_identity_changed"
        final_pass = False
    if actual_providers and set(actual_providers) != {expected_provider}:
        stop_reason = "model_identity_changed"
        final_pass = False
    clean_closure = int(
        condition == "pd-vista" and final_pass and disclosure_count > 0 and access_rounds > 0
    )
    usage = {
        key: sum(int(turn["event_summary"].get(key) or 0) for turn in turns)
        for key in (
            "requests", "tool_uses", "input_tokens", "output_tokens", "cache_read_tokens"
        )
    }
    quarantined = stop_reason in {
        "provider_policy_rejection", "api_error", "session_identity_changed",
        "verifier_infrastructure_error", "model_identity_changed",
    }
    status = (
        "passed_with_clean_vista_feedback_loop" if clean_closure
        else "passed" if final_pass and condition != "pd-vista"
        else "passed_without_clean_vista_feedback_loop" if final_pass
        else f"quarantined_{stop_reason}" if quarantined
        else "failed_max_turns"
    )
    result = {
        "schema_version": 1,
        "run_id": f"{RUN_PREFIX}-{condition}-replicate-{replicate:02d}",
        "task_id": "task-007-travel-planning", "replicate_id": replicate,
        "condition": condition, "backend": "opencode-local-current-config",
        "docker_used": False, "requested_provider_model": requested_model,
        "expected_actual_model": expected_model,
        "actual_model_ids": actual_models,
        "expected_provider": expected_provider,
        "actual_provider_ids": actual_providers,
        "initially_available_modules": list(MODULE_IDS) if condition == "native" else [],
        "usage": usage,
        "session_id": session_id, "turns": turns, "disclosed_modules": disclosed,
        "accessed_modules_after_disclosure": list(dict.fromkeys(accessed_after_disclosure)),
        "distractor_flight_disclosed": "search-flights" in disclosed,
        "vista_metrics": {
            "trigger_opportunities": trigger_opportunities,
            "routable_feedback": routable_feedback,
            "successful_disclosures": disclosure_count,
            "next_round_module_access": access_rounds,
            "clean_complete_closure": clean_closure,
        },
        "final_verifier_pass": final_pass, "stop_reason": stop_reason,
        "status": status, "workspace": str(workspace),
    }
    (result_dir / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return result


def main() -> int:
    config_path, requested_model, api_key_env = current_opencode_config()
    if api_key_env and not os.environ.get(api_key_env):
        raise RuntimeError(
            f"the current OpenCode model {requested_model} requires {api_key_env} "
            "in this process environment"
        )
    launcher = freeze_launcher()
    if RESULT_ROOT.exists() and (
        (RESULT_ROOT / "group-summary.json").exists()
        or any(RESULT_ROOT.glob("*/replicate-*"))
    ):
        raise RuntimeError(f"refusing to reuse a result root with experiment evidence: {RESULT_ROOT}")
    RESULT_ROOT.mkdir(parents=True, exist_ok=True)
    ready, readiness_output = verifier_readiness()
    (RESULT_ROOT / "verifier-readiness.txt").write_text(readiness_output, encoding="utf-8")
    if not ready:
        summary = {
            "schema_version": 1, "protocol": PROTOCOL,
            "task_id": "task-007-travel-planning", "conditions": list(CONDITIONS),
            "replicates_per_condition": 3, "planned_trials": 9,
            "completed_trials": 0, "requested_provider_model": requested_model,
            "status": "quarantined_verifier_readiness",
        }
        (RESULT_ROOT / "group-summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return 2
    results: list[dict[str, Any]] = []
    for replicate, condition in SCHEDULE:
        result = run_replicate(
            condition,
            replicate,
            config_path=config_path,
            requested_model=requested_model,
        )
        results.append(result)
        if result["status"].startswith("quarantined_"):
            break
    condition_summaries = {}
    for condition in CONDITIONS:
        condition_results = [item for item in results if item["condition"] == condition]
        condition_summaries[condition] = {
            "planned_trials": 3,
            "completed_trials": len(condition_results),
            "verifier_passes": sum(bool(item["final_verifier_pass"]) for item in condition_results),
            "quarantined_trials": sum(
                item["status"].startswith("quarantined_") for item in condition_results
            ),
            "usage": {
                key: sum(int(item["usage"][key]) for item in condition_results)
                for key in (
                    "requests", "tool_uses", "input_tokens", "output_tokens",
                    "cache_read_tokens",
                )
            },
        }
    summary = {
        "schema_version": 1, "protocol": PROTOCOL,
        "task_id": "task-007-travel-planning", "conditions": list(CONDITIONS),
        "replicates_per_condition": 3, "planned_trials": 9,
        "completed_trials": len(results), "schedule": [
            {"replicate_id": replicate, "condition": condition}
            for replicate, condition in SCHEDULE
        ],
        "requested_provider_model": requested_model,
        "condition_summaries": condition_summaries,
        "opencode": {
            "config_path": str(config_path),
            "launcher": launcher.launcher,
            "version": launcher.version,
            "binary_sha256": launcher.binary_sha256,
        },
        "results": [{
            "condition": item["condition"], "replicate_id": item["replicate_id"],
            "status": item["status"],
            "final_verifier_pass": item["final_verifier_pass"],
            "session_id": item["session_id"],
            "actual_model_ids": item["actual_model_ids"],
            "usage": item["usage"],
            "initially_available_modules": item["initially_available_modules"],
            "disclosed_modules": item["disclosed_modules"],
            "accessed_modules_after_disclosure": item["accessed_modules_after_disclosure"],
            "distractor_flight_disclosed": item["distractor_flight_disclosed"],
            "vista_metrics": item["vista_metrics"],
        } for item in results],
    }
    (RESULT_ROOT / "group-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return 0 if len(results) == 9 else 1


if __name__ == "__main__":
    raise SystemExit(main())
