import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_inspect_schema_runs_on_fake_fixtures():
    r = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "inspect_schema.py"),
         "--raw-dir", str(ROOT / "tests" / "fixtures")],
        capture_output=True, text=True, timeout=120,
    )
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert "failed:" not in out
    assert "## patient_information" in out
    assert "art MAP<65 >5min: 1" in out
    assert "has vasopressor: 1" in out
    # inspector must not echo identifiers
    assert "FAKE0001" not in out
