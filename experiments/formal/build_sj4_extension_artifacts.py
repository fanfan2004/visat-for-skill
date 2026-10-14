"""Deterministic, source-preserving SkillJuror-style artifacts for task-014/015.

No model calls or task/oracle content used to construct guidance. Identity hashes
of task inputs and the verifier are recorded afterward. Existing
files are accepted only if byte-identical; use a new output root for a new version.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

import yaml

from sj4_extension_tasks import TASKS

REPO = Path(__file__).resolve().parents[2]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def files(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): sha(p) for p in sorted(root.rglob("*"))
            if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"}


def put(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() != content:
        raise RuntimeError(f"refusing to replace a different artifact: {path}")
    path.write_bytes(content)


def text(path: Path, content: str) -> None:
    put(path, content.replace("\r\n", "\n").encode("utf-8"))


def flattened(root: Path) -> str:
    result = (root / "SKILL.md").read_text(encoding="utf-8").rstrip() + "\n"
    for support in sorted(root.rglob("*")):
        if not support.is_file() or support.name == "SKILL.md":
            continue
        relative = support.relative_to(root).as_posix()
        payload = support.read_text(encoding="utf-8").rstrip()
        if support.suffix == ".md":
            result += f"\n## Support content from `{relative}`\n\n{payload}\n"
        else:
            language = "python" if support.suffix == ".py" else "text"
            fence = "`" * max(3, max((len(x) for x in re.findall(r"`+", payload)), default=0) + 1)
            result += (f"\n## Support content from `{relative}`\n\n"
                       f"Recreate `{relative}` from the embedded content before using it.\n\n"
                       f"{fence}{language}\n{payload}\n{fence}\n")
    return result


def build(task_id: str, source_tasks: Path, output_root: Path) -> dict:
    spec = TASKS[task_id]
    source = source_tasks / spec["benchmark"] / "environment" / "skills"
    destination = output_root / task_id
    expected = set(spec["modules"])
    actual = {p.name for p in source.iterdir() if p.is_dir() and (p / "SKILL.md").exists()}
    if expected != actual:
        raise RuntimeError(f"source module set differs for {task_id}: {actual}")
    bundle = []
    entry = (f"---\nname: {spec['benchmark']}-guidance\n"
             f"description: Guidance for {spec['benchmark']} workflows.\n---\n\n"
             f"# {spec['benchmark']} guidance\n\n"
             "Read detailed guidance when the corresponding work is needed:\n\n")
    modules = []
    for name, module in spec["modules"].items():
        root = source / name
        flat = flattened(root)
        text(destination / "flat" / name / "SKILL.md", flat)
        bundle.append(f"# {name}\n\n{flat}")
        reference = (root / "SKILL.md").read_text(encoding="utf-8")
        if name == "xlsx":
            # Preserve runnable helper code byte-for-byte and canonicalize its commands.
            reference = reference.replace("python recalc.py", "python xlsx/recalc.py")
            reference = reference.replace("LICENSE.txt has", "xlsx/LICENSE.txt has")
        text(destination / "pd" / "references" / f"{name}.md", reference)
        for support in sorted(root.rglob("*")):
            if support.is_file() and support.name != "SKILL.md":
                put(destination / "pd" / name / support.relative_to(root), support.read_bytes())
        entry += (f"- **`references/{name}.md`** — Open when you need to "
                  f"{module['description'][0].lower()}{module['description'][1:]}\n")
        modules.append({"id": name, "path": f"pd/references/{name}.md",
                        "triggers": module["triggers"]})
    entry += "\nRun helper commands from this skill directory using the paths in the references.\n"
    text(destination / "pd" / "SKILL.md", entry)
    text(destination / "single-file" / "SKILL.md", "\n\n".join(bundle) + "\n")
    text(destination / "manifest.yaml", yaml.safe_dump({
        "schema_version": 1,
        "construction_model": "none-deterministic-source-preserving-v1",
        "source_content_equivalence": "source-hash-and-support-byte-equality",
        "modules": modules,
    }, sort_keys=False))
    # Manifest of input/verifier identities, without exposing their content to skills.
    task = source.parent.parent
    report = {
        "schema_version": 1, "task_id": task_id, "benchmark": spec["benchmark"],
        "construction": "deterministic-source-preserving-v1",
        "source_files": files(source),
        "benchmark_files": {name: sha(task / name) for name in [
            "task.md", "verifier/test_outputs.py",
            *("environment/" + name for name in spec["inputs"]),
        ]},
        "artifact_files": {p: digest for p, digest in files(destination).items()
                           if p != "construction-evidence.json"},
        "path_rewrites": ["xlsx reference: python recalc.py -> python xlsx/recalc.py",
                          "xlsx reference: LICENSE.txt -> xlsx/LICENSE.txt"]
                         if task_id == "task-015" else [],
        "limitations": ["xlsx Skill requires formulas; original verifier loads formulas as text"]
                       if task_id == "task-015" else [],
    }
    text(destination / "construction-evidence.json", json.dumps(report, indent=2) + "\n")
    return report


def verify_artifacts(task: Path, artifact: Path) -> bool:
    try:
        report = json.loads((artifact / "construction-evidence.json").read_text(encoding="utf-8"))
        source = task / "environment" / "skills"
        return (files(source) == report["source_files"]
                and all((task / p).is_file() and sha(task / p) == digest
                        for p, digest in report["benchmark_files"].items())
                and {p: digest for p, digest in files(artifact).items()
                     if p != "construction-evidence.json"} == report["artifact_files"])
    except (OSError, ValueError, KeyError):
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-tasks", type=Path,
                        default=REPO / "experiments/benchmarks/sj4-extension-v1")
    parser.add_argument("--output-root", type=Path, default=REPO / "experiments/artifacts/sj4")
    parser.add_argument("--task", choices=TASKS)
    parser.add_argument("--check", action="store_true", help="verify hashes without writing anything")
    args = parser.parse_args()
    for task_id in ([args.task] if args.task else TASKS):
        if not args.check:
            build(task_id, args.source_tasks, args.output_root)
        ok = verify_artifacts(args.source_tasks / TASKS[task_id]["benchmark"],
                              args.output_root / task_id)
        print(f"{task_id}: source/artifact hashes {'OK' if ok else 'FAILED'}")
        if not ok:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
