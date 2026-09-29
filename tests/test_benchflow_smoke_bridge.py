from pathlib import Path

ROOT = Path(__file__).parents[1]
BRIDGE = ROOT / "experiments" / "benchflow" / "smoke.sh"
PREFLIGHT = ROOT / "experiments" / "benchflow" / "preflight.sh"
PROVIDER_PREFLIGHT = ROOT / "experiments" / "benchflow" / "provider_preflight.py"
PROXY_PREFLIGHT = ROOT / "experiments" / "benchflow" / "proxy_preflight.py"
MANIFEST = ROOT / "experiments" / "benchflow" / "smoke.yaml"
README = ROOT / "experiments" / "benchflow" / "README.md"
PROMPTS = ROOT / "experiments" / "benchflow" / "task_prompts"


def test_bridge_uses_openhands_with_chat_completions_only() -> None:
    text = BRIDGE.read_text(encoding="utf-8")

    assert "eval run" in text
    assert "--agent openhands" in text
    assert "--sandbox docker" in text
    assert '"BENCHFLOW_PROVIDER_PROTOCOL=openai-completions"' in text
    assert '"LLM_MODEL=$LLM_MODEL"' in text
    assert '"LLM_BASE_URL=$LLM_BASE_URL"' in text
    assert '"LLM_API_KEY=$LLM_API_KEY"' in text
    assert "codex-acp" not in text
    assert "CODEX_CONFIG" not in text
    assert "openai-responses" not in text


def test_bridge_isolates_each_task_trial_and_condition() -> None:
    text = BRIDGE.read_text(encoding="utf-8")

    assert 'TRIAL_ID="${VISTA_SMOKE_TRIAL_ID:-trial-001}"' in text
    assert 'TASK_RUN_ROOT="$RUN_ROOT/$TASK_ID/$TRIAL_ID/$condition"' in text
    assert 'WORKSPACE_ROOT="$TASK_RUN_ROOT/workspace"' in text
    assert 'PROMPT_ROOT="$TASK_RUN_ROOT/prompts"' in text
    assert 'SKILLS_ROOT="$TASK_RUN_ROOT/skills"' in text
    assert 'LOG_ROOT="$TASK_RUN_ROOT/logs"' in text
    assert 'RESULT_ROOT="$TASK_RUN_ROOT/results"' in text
    assert 'TEMP_ROOT="$TASK_RUN_ROOT/tmp"' in text
    assert '--jobs-dir "$RESULT_ROOT"' in text
    assert '--context-root "$WORKSPACE_ROOT"' in text
    assert 'env -u LLM_MODEL -u LLM_BASE_URL -u LLM_API_KEY' in text


def test_no_skill_does_not_materialize_skills_or_task_prompts() -> None:
    text = BRIDGE.read_text(encoding="utf-8")

    assert 'noskill)' in text
    assert 'common_args+=(--skill-mode no-skill)' in text
    assert 'native)' in text
    assert 'cp -a "$TASK_DIR/environment/skills/." "$SKILLS_ROOT/"' in text
    assert 'cp "$TASK_PROMPT_DIR/design.md" "$PROMPT_ROOT/design.md"' in text
    assert 'cp "$TASK_PROMPT_DIR/professional_revision.md"' in text
    assert 'cp "$PROMPT_ROOT/design.md" "$WORKSPACE_ROOT/task_prompts/design.md"' in text
    revision_copy = (
        'cp "$PROMPT_ROOT/professional_revision.md" '
        '"$WORKSPACE_ROOT/task_prompts/professional_revision.md"'
    )
    assert revision_copy in text
    assert 'common_args+=(--skill-mode with-skill --skills-dir "$SKILLS_ROOT")' in text


def test_preflight_checks_openhands_and_safe_chat_completions_config() -> None:
    text = PREFLIGHT.read_text(encoding="utf-8")

    assert "LLM_API_KEY" in text
    assert "LLM_BASE_URL" in text
    assert "provider_preflight.py" in text
    assert "bench agent show openhands" in text
    assert "codex-acp" not in text
    assert "docker info" in text
    assert "printf '%s' \"${OPENAI_API_KEY}" not in text


def test_provider_preflight_maps_only_openhands_chat_completion_environment() -> None:
    text = PROVIDER_PREFLIGHT.read_text(encoding="utf-8")

    assert '"openhands"' in text
    assert "LLM_MODEL" in text
    assert "LLM_BASE_URL" in text
    assert "LLM_API_KEY" in text
    assert "openai-completions" in text
    assert 'if "CODEX_CONFIG" in resolved' in text
    assert "openai-responses" not in text
    assert "print(api_key)" not in text
    assert "127.0.0.1" in text
    assert "benchflow-preflight-fake-key" in text


def test_task_prompts_are_task_scoped_not_global() -> None:
    assert (PROMPTS / "task-002" / "design.md").is_file()
    assert (PROMPTS / "task-002" / "professional_revision.md").is_file()
    assert not (PROMPTS / "design.md").exists()
    assert not (PROMPTS / "professional_revision.md").exists()


def test_pd_vista_is_fail_fast_and_not_a_static_skills_dir_alias() -> None:
    bridge = BRIDGE.read_text(encoding="utf-8")
    manifest = MANIFEST.read_text(encoding="utf-8")
    assert "PD-VISTA smoke is intentionally blocked" in bridge
    assert "unsupported_by_static_bridge" in manifest


def test_vendor_repositories_have_no_tracked_or_untracked_changes() -> None:
    # The test documents the immutable vendor boundary; the actual git-status
    # assertion runs in the verification command because it requires git.
    assert (ROOT / "vendor" / "skillsbench" / ".git").exists()
    assert (ROOT / "vendor" / "skill-juror" / ".git").exists()


def test_smoke_target_is_the_light_task_and_task_004_remains_in_manifest() -> None:
    manifest = MANIFEST.read_text(encoding="utf-8")
    smoke = BRIDGE.read_text(encoding="utf-8")
    pilot_manifest = (ROOT / "tasks" / "manifest.yaml").read_text(encoding="utf-8")

    assert "enterprise-information-search" in manifest
    assert "task-002" in smoke
    assert "powerlifting-coef-calc" in pilot_manifest
