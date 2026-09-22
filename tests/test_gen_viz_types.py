"""Task 7: the generated contract artifacts — shared/rows.generated.ts and
schema/schema.sql — must be up to date with db_schema. Regenerate in check
mode; a diff fails the build (drift fails CI, not production)."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GEN = ROOT / "scripts" / "gen_viz_types.py"


def test_generated_artifacts_are_current():
    r = subprocess.run(
        [sys.executable, str(GEN), "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, (
        "generated contract artifacts drifted from db_schema:\n"
        f"{r.stdout}\n{r.stderr}"
        "\nRun scripts/gen_viz_types.py to regenerate."
    )
