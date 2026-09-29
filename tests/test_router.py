from __future__ import annotations

from pathlib import Path

import pytest

from vista.feedback import FeedbackObservation, FeedbackParser, FeedbackSource
from vista.runtime import DisclosureRouter, ManifestError, SkillManifest


def _write_skill(root: Path) -> Path:
    references = root / "references"
    references.mkdir(parents=True)
    (references / "validation.md").write_text("Run the public tests.", encoding="utf-8")
    (references / "repair.md").write_text("Repair only observed failures.", encoding="utf-8")
    manifest = root / "manifest.yaml"
    manifest.write_text(
        """modules:
  - id: validation
    path: references/validation.md
    triggers: [test_failure, assertion_error]
  - id: repair
    path: references/repair.md
    triggers: [repeated_failure]
""",
        encoding="utf-8",
    )
    return manifest


def test_routes_in_manifest_order_and_avoids_repeat_access(tmp_path: Path) -> None:
    manifest = SkillManifest.load(_write_skill(tmp_path / "skill"))
    parser = FeedbackParser()
    observation = FeedbackObservation(
        source=FeedbackSource.TEST_OUTPUT,
        text="AssertionError: 1 test failed",
        exit_code=1,
    )
    parser.parse(observation, trial_id="trial")
    repeated = parser.parse(observation, trial_id="trial")

    decision = DisclosureRouter(manifest).route(
        repeated,
        previous_modules=("validation",),
    )
    disclosed = DisclosureRouter(manifest).disclose(decision)

    assert decision.selected_modules == ("repair",)
    assert decision.skipped_previous_modules == ("validation",)
    assert disclosed[0].content == "Repair only observed failures."
    assert disclosed[0].estimated_tokens > 0


def test_rejects_manifest_path_escape(tmp_path: Path) -> None:
    skill = tmp_path / "skill"
    skill.mkdir()
    (tmp_path / "secret.md").write_text("private", encoding="utf-8")
    manifest = skill / "manifest.yaml"
    manifest.write_text(
        """modules:
  - id: unsafe
    path: ../secret.md
    triggers: [test_failure]
""",
        encoding="utf-8",
    )

    with pytest.raises(ManifestError, match="escapes"):
        SkillManifest.load(manifest)


def test_manifest_reports_unroutable_feedback_types(tmp_path: Path) -> None:
    manifest = SkillManifest.load(_write_skill(tmp_path / "skill"))

    assert manifest.unroutable_feedback_types(
        ("test_failure", "schema_error", "repeated_failure")
    ) == ("schema_error",)


def test_router_prefers_specific_feedback_over_generic_test_failure(tmp_path: Path) -> None:
    skill = tmp_path / "skill"
    (skill / "references").mkdir(parents=True)
    (skill / "references" / "schema.md").write_text("schema", encoding="utf-8")
    (skill / "references" / "tests.md").write_text("tests", encoding="utf-8")
    manifest = skill / "manifest.yaml"
    manifest.write_text(
        """modules:
  - id: schema
    path: references/schema.md
    triggers: [schema_error]
  - id: generic-tests
    path: references/tests.md
    triggers: [test_failure]
""",
        encoding="utf-8",
    )

    decision = DisclosureRouter(SkillManifest.load(manifest)).route(
        ("schema_error", "test_failure")
    )

    assert decision.selected_modules == ("schema",)
