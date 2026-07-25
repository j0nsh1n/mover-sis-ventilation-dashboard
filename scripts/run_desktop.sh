#!/usr/bin/env bash
# Launch the MOVER SIS native desktop application.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ -f "$ROOT/.venv/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "$ROOT/.venv/bin/activate"
fi

export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"

# Prefer offscreen-safe libs on some Linux setups when DISPLAY is set
exec python -m src.desktop "$@"
