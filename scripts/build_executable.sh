#!/usr/bin/env bash
# Build a standalone Linux x86_64 executable for Nobara / Fedora.
# Output: dist/MOVER-SIS-Monitor/  and  dist/MOVER-SIS-Monitor-linux-x86_64.tar.gz
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ -f "$ROOT/.venv/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "$ROOT/.venv/bin/activate"
fi

export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"

echo "==> Installing build dependencies"
python -m pip install -q -U pip
python -m pip install -q -r requirements.txt
python -m pip install -q 'pyinstaller>=6.3'

echo "==> Cleaning previous build"
rm -rf build/pyinstaller dist/MOVER-SIS-Monitor dist/MOVER-SIS-Monitor-linux-x86_64.tar.gz

echo "==> Running PyInstaller (onedir — required for QtWebEngine)"
python -m PyInstaller \
  --noconfirm \
  --clean \
  --distpath dist \
  --workpath build/pyinstaller \
  packaging/mover_sis_monitor.spec

APP_DIR="$ROOT/dist/MOVER-SIS-Monitor"
BIN="$APP_DIR/MOVER-SIS-Monitor"

if [[ ! -x "$BIN" && -f "$BIN" ]]; then
  chmod +x "$BIN"
fi

# Repair Qt library clashes: replace top-level system libQt6* copies with
# symlinks into the PySide6-bundled Qt (avoids Qt_PRIVATE_API version errors).
INTERNAL="$APP_DIR/_internal"
if [[ -d "$INTERNAL/PySide6/Qt/lib" ]]; then
  echo "==> Linking top-level Qt libs to PySide6 bundle"
  shopt -s nullglob
  for f in "$INTERNAL"/libQt6*.so*; do
    base="$(basename "$f")"
    target="$INTERNAL/PySide6/Qt/lib/$base"
    if [[ -e "$target" ]]; then
      rm -f "$f"
      ln -s "PySide6/Qt/lib/$base" "$f"
    fi
  done
  shopt -u nullglob
fi

# Place data placeholders next to the binary so users know where EMR goes
mkdir -p "$APP_DIR/data/raw/EMR" "$APP_DIR/data/processed"
if [[ -f "$ROOT/data/README.md" ]]; then
  cp -f "$ROOT/data/README.md" "$APP_DIR/data/README.md"
fi
cat > "$APP_DIR/data/raw/EMR/README.txt" <<'EOF'
Place SIS EMR CSV files here:

  patient_information.csv
  patient_ventilator.csv
  patient_vitals.csv

Then launch MOVER-SIS-Monitor and click "Run / reload pipeline".
EOF

cat > "$APP_DIR/README-RUN.txt" <<'EOF'
MOVER SIS Ventilation Monitor — Linux executable
================================================

Run:
  ./MOVER-SIS-Monitor

Data:
  Put SIS EMR CSVs in:  ./data/raw/EMR/
  Processed caches go to: ./data/processed/

Optional environment overrides:
  MOVER_DATA_DIR=/path/to/data
  MOVER_EMR_DIR=/path/to/EMR
  MOVER_PROCESSED_DIR=/path/to/processed

System libraries (Nobara/Fedora usually already have these via the
bundled Qt; if the app fails to start, install):
  sudo dnf install -y mesa-libGL libxkbcommon xcb-util-cursor \
    xcb-util-wm xcb-util-keysyms xcb-util-image xcb-util-renderutil \
    libnsl libxcrypt-compat

Not for clinical care. Research use on de-identified MOVER SIS data only.
EOF

# Desktop file for local installs
cat > "$APP_DIR/mover-sis-monitor.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=MOVER SIS Ventilation Monitor
Comment=Intraoperative ventilation & anesthesia research dashboard
Exec=$APP_DIR/MOVER-SIS-Monitor
Path=$APP_DIR
Terminal=false
Categories=Science;Education;
StartupNotify=true
EOF

echo "==> Creating tarball"
tar -C "$ROOT/dist" -czf "$ROOT/dist/MOVER-SIS-Monitor-linux-x86_64.tar.gz" MOVER-SIS-Monitor

echo
echo "Build complete."
echo "  App folder : $APP_DIR"
echo "  Binary     : $BIN"
echo "  Tarball    : $ROOT/dist/MOVER-SIS-Monitor-linux-x86_64.tar.gz"
echo
echo "Launch:  $BIN"
ls -lh "$BIN" "$ROOT/dist/MOVER-SIS-Monitor-linux-x86_64.tar.gz" 2>/dev/null || true
