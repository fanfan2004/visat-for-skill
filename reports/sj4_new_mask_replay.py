"""Replay the NEW mask logic from the working-tree runner on ALL historical
verifier outputs; count files, confirm zero leaks."""
import re
from pathlib import Path

REPO = Path(r"E:\git-hub\vista-mac\visat-for-skill")
OUT = REPO / "reports" / "_mask_replay_out.txt"
lines = []
def w(s=""):
    lines.append(str(s))

# extract the new mask/diagnostic code from the working runner
src = (REPO / "experiments" / "formal" / "run_sj4_zcode_group.py").read_text(encoding="utf-8")
start = src.index("_LITERAL_OPERAND")
end = src.index("def run_verifier")
block = src[start:end]
ns = {"re": re}
exec(block, ns)
mask_assert_values = ns["mask_assert_values"]
public_diagnostic = ns["public_diagnostic"]

LEAK = [
    re.compile(r"assert\s+.{0,60}\d[\d,.]*\s*==\s*\d"),
    re.compile(r"assert\s+\S[^=]*\s*==\s*'[^']+'"),
    re.compile(r"assert\s+'[^']*'\s*==\s*\S+"),
    re.compile(r"[Ee]xpected\s+'[^']+'"),
    re.compile(r"[Ee]xpected\s+\d"),
    re.compile(r"Got\s+'[^']+'"),
]
total = leaked = 0
leak_files = []
for vf in (REPO / "data" / "raw").glob("**/verifier-turn-*.txt"):
    raw_out = vf.read_text(encoding="utf-8", errors="replace")
    if not any(m in raw_out for m in ("assert", "Expected", "expected", "Got", "mismatch")):
        continue
    total += 1
    diag = public_diagnostic(raw_out)
    hits = [p.pattern for p in LEAK if p.search(diag)]
    if hits:
        leaked += 1
        leak_files.append(f"{vf.relative_to(REPO / 'data' / 'raw')} :: {hits}")

w(f"verifier outputs scanned (with assert/Expected content): {total}")
w(f"leaks found in fed diagnostic: {leaked}")
for f in leak_files[:20]:
    w("  " + f)

# show the fed diagnostic for the two known leak cases with the NEW logic
w()
w("=== new-logic fed diagnostic for known leak cases ===")
for rel in (r"formal-task-011-zcode-local-4cond-x5-glm53-20261013\noskill\replicate-01\verifier-turn-1.txt",
            r"formal-task-012-zcode-local-4cond-x5-glm53-20261013\pd-vista\replicate-03\verifier-turn-1.txt"):
    raw_out = (REPO / "data" / "raw" / rel).read_text(encoding="utf-8", errors="replace")
    diag = public_diagnostic(raw_out)
    w(f"--- {rel}")
    for l in diag.splitlines()[:8]:
        w("  | " + l[:170])

OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
print("written", OUT)
