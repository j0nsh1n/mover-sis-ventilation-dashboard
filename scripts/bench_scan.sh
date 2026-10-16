#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <emr-dir> [workers...]" >&2
  exit 2
fi
if [[ ! -d "$1" ]]; then
  echo "EMR folder is missing: $1" >&2
  exit 2
fi
EMR_DIR="$(cd "$1" && pwd)"
shift
if [[ $# -eq 0 ]]; then
  set -- 1 4 6 8
fi
for workers in "$@"; do
  if [[ ! "$workers" =~ ^[1-9][0-9]*$ ]]; then
    echo "Worker counts must be positive integers: $workers" >&2
    exit 2
  fi
done

cd "$ROOT"
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
mkdir -p "$ROOT/calibration"
OUT="$(mktemp -d "$ROOT/calibration/bench-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")"
for workers in "$@"; do
  python -m src.pipeline.corpus_scan --emr-dir "$EMR_DIR" \
    --output-dir "$OUT/w$workers" --preset default --workers "$workers" \
    > "$OUT/w$workers.log" 2>&1
done

python - "$EMR_DIR" "$OUT" "$@" <<'PY'
import json
import os
import shlex
import sys
from pathlib import Path

emr, output, *workers = sys.argv[1:]
root = Path(output)
lines = [
    "# Full-EMR scan benchmark", "",
    f"Host cores: {os.cpu_count() or 1}", "",
    "| Workers requested | Workers used | Shards | Seconds | Peak RSS parent (MB) | Peak RSS largest worker (MB) | Scanned cases |",
    "|---|---|---|---|---|---|---|",
]
for worker in workers:
    meta = json.loads((root / f"w{worker}" / "corpus_meta.json").read_text())
    rss = meta["peak_rss_mb"]
    row = [meta["workers_requested"], meta["workers"], meta["n_shards"], meta["seconds"],
           rss["parent"], rss["largest_worker"], meta["n_scanned_cases"]]
    lines.append("| " + " | ".join(map(str, row)) + " |")
lines += [
    "", "RSS values are process-lifetime high-water marks. A serial scan scores in the parent;",
    "its largest-worker RSS is zero. None means resource measurements are unavailable.",
    "", "## Commands", "", "```bash",
]
for worker in workers:
    lines.append(shlex.join([
        "python", "-m", "src.pipeline.corpus_scan", "--emr-dir", emr,
        "--output-dir", str(root / f"w{worker}"), "--preset", "default", "--workers", worker,
    ]))
lines += ["```", ""]
report = root / "bench.md"
report.write_text("\n".join(lines), encoding="utf-8")
print(report)
PY
