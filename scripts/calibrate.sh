#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ $# -ne 2 ]]; then
  echo "Usage: $0 <emr-dir> <name>" >&2
  exit 2
fi
if [[ ! -d "$1" ]]; then
  echo "EMR folder is missing: $1" >&2
  exit 2
fi
if [[ ! "$2" =~ ^[[:alnum:]][[:alnum:]_.-]*$ ]]; then
  echo "Calibration name must contain only letters, digits, dots, underscores or hyphens." >&2
  exit 2
fi
EMR_DIR="$(cd "$1" && pwd)"
OUT="$ROOT/calibration/$2"
SEED="${CALIBRATION_SEED:-42}"
PRESET="${CALIBRATION_PRESET:-default}"
N_CASES="${CALIBRATION_N_CASES:-50}"
cd "$ROOT"
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
mkdir -p "$OUT"
python - "$EMR_DIR" "$OUT/processed" "$SEED" "$PRESET" "$N_CASES" <<'PY'
import sys
from src.pipeline.run import run_pipeline

emr, output, seed, preset, n_cases = sys.argv[1:]
run_pipeline(emr_dir=emr, output_dir=output, seed=int(seed), preset=preset,
             n_cases=int(n_cases), write_sample_csv=False)
PY
python -m src.pipeline.profile --processed-dir "$OUT/processed" \
  --preset "$PRESET" --seed "$SEED" --out "$OUT/profile.md" --json "$OUT/profile.json"
