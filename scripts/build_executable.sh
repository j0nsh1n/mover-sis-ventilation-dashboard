#!/usr/bin/env bash
# Build a standalone Linux x86_64 executable for Nobara / Fedora.
# Output: dist/MOVER-SIS-Monitor/  and  dist/MOVER-SIS-Monitor-vX.Y.Z-linux-x86_64.tar.gz
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ -f "$ROOT/.venv/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "$ROOT/.venv/bin/activate"
fi

export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"

VERSION="$(tr -d '[:space:]' < "$ROOT/VERSION")"
export MOVER_APP_VERSION="$VERSION"
echo "==> Building MOVER SIS Monitor v${VERSION}"

echo "==> Installing build dependencies"
python -m pip install -q -U pip
python -m pip install -q -r requirements.txt
python -m pip install -q 'pyinstaller>=6.3'

echo "==> Cleaning previous build"
rm -rf build/pyinstaller dist/MOVER-SIS-Monitor
rm -f dist/MOVER-SIS-Monitor-*.tar.gz dist/MOVER-SIS-Monitor-linux-x86_64.tar.gz

echo "==> Running PyInstaller (onedir)"
python -m PyInstaller \
  --noconfirm \
  --clean \
  --distpath dist \
  --workpath build/pyinstaller \
  packaging/mover_sis_monitor.spec

APP_DIR="$ROOT/dist/MOVER-SIS-Monitor"
BIN="$APP_DIR/MOVER-SIS-Monitor"
TARBALL="$ROOT/dist/MOVER-SIS-Monitor-v${VERSION}-linux-x86_64.tar.gz"

if [[ ! -x "$BIN" && -f "$BIN" ]]; then
  chmod +x "$BIN"
fi

# Repair Qt library clashes
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

# Stamp version into the app bundle
cp -f "$ROOT/VERSION" "$APP_DIR/VERSION"
echo "v${VERSION}" > "$APP_DIR/BUILD_INFO.txt"
{
  echo "version=${VERSION}"
  echo "built_at=$(date -Iseconds)"
  echo "host=$(hostname 2>/dev/null || true)"
  echo "python=$(python - <<'PY'
import sys; print(sys.version.split()[0])
PY
)"
} >> "$APP_DIR/BUILD_INFO.txt"

mkdir -p "$APP_DIR/data/raw/EMR" "$APP_DIR/data/processed"
if [[ -f "$ROOT/data/README.md" ]]; then
  cp -f "$ROOT/data/README.md" "$APP_DIR/data/README.md"
fi
cat > "$APP_DIR/data/raw/EMR/README.txt" <<'EOF'
Place SIS EMR CSV files here:

  patient_information.csv
  patient_ventilator.csv
  patient_vitals.csv

Then launch MOVER-SIS-Monitor and choose this folder (or Run pipeline).
EOF

cat > "$APP_DIR/README-RUN.txt" <<EOF
MOVER SIS Ventilation Monitor v${VERSION} — Linux executable
============================================================

Run:
  ./launch.sh
  # or via launcher after install_local.sh

System library (usually present on a desktop install; launch.sh checks):
  Ubuntu / Debian:  sudo apt install libxkbcommon-x11-0
  Fedora / Nobara:  sudo dnf install libxkbcommon-x11

Data:
  Put SIS EMR CSVs in:  ./data/raw/EMR/
  Or pick any EMR folder in the app (Browse…)
  Processed caches go to: ./data/processed/ (or next to your data root)

Optional environment overrides:
  MOVER_DATA_DIR=/path/to/data
  MOVER_EMR_DIR=/path/to/EMR
  MOVER_PROCESSED_DIR=/path/to/processed

Not for clinical care. Research use on de-identified MOVER SIS data only.
EOF

# Desktop file template (install_local rewrites paths)
cat > "$APP_DIR/mover-sis-monitor.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=MOVER SIS Monitor
GenericName=Ventilation & Anesthesia Monitor
Comment=MOVER SIS research dashboard v${VERSION}
Exec=$APP_DIR/launch.sh
Path=$APP_DIR
Terminal=false
Categories=Science;Education;
StartupNotify=true
Version=${VERSION}
EOF

# Prefer frozen binary when launched from this folder
cat > "$APP_DIR/launch.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
APP_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-xcb}"
export QTWEBENGINE_DISABLE_SANDBOX=1
export QTWEBENGINE_CHROMIUM_FLAGS="${QTWEBENGINE_CHROMIUM_FLAGS:---no-sandbox --disable-gpu-sandbox}"
# User-selected EMR/wave trees may live outside the install dir (e.g. /var/mnt/games)
export MOVER_ALLOW_EXTERNAL_OUTPUT="${MOVER_ALLOW_EXTERNAL_OUTPUT:-1}"
export LD_LIBRARY_PATH="$APP_DIR/_internal/PySide6/Qt/lib:$APP_DIR/_internal/numpy.libs:$APP_DIR/_internal/scipy.libs:$APP_DIR/_internal/pillow.libs:$APP_DIR/_internal/PIL.libs:$APP_DIR/_internal/matplotlib.libs:$APP_DIR/_internal/shiboken6:${LD_LIBRARY_PATH:-}"

# Qt's X11 plugin needs these from the system; without them it fails with an
# unreadable "could not load the Qt platform plugin" message. Say what to install.
if [[ "$QT_QPA_PLATFORM" == xcb* ]] && command -v ldconfig >/dev/null 2>&1; then
  host_libs="$(ldconfig -p 2>/dev/null || true)"
  missing=()
  for lib in libxkbcommon.so.0 libxkbcommon-x11.so.0 libxcb-cursor.so.0; do
    [[ -e "$APP_DIR/_internal/$lib" || "$host_libs" == *"$lib "* ]] || missing+=("$lib")
  done
  if (( ${#missing[@]} )); then
    msg="MOVER SIS Monitor needs system libraries that are not installed: ${missing[*]}

Ubuntu / Debian:  sudo apt install libxkbcommon0 libxkbcommon-x11-0 libxcb-cursor0
Fedora / Nobara:  sudo dnf install libxkbcommon libxkbcommon-x11 xcb-util-cursor"
    echo "$msg" >&2
    if command -v kdialog >/dev/null 2>&1; then
      kdialog --error "$msg" || true
    elif command -v zenity >/dev/null 2>&1; then
      zenity --error --text="$msg" || true
    fi
    exit 1
  fi
fi

cd "$APP_DIR"
exec "$APP_DIR/MOVER-SIS-Monitor" "$@"
EOF
chmod +x "$APP_DIR/launch.sh"

echo "==> Creating tarball $TARBALL"
tar -C "$ROOT/dist" -czf "$TARBALL" MOVER-SIS-Monitor
# Stable alias for tooling
cp -f "$TARBALL" "$ROOT/dist/MOVER-SIS-Monitor-linux-x86_64.tar.gz"

echo
echo "Build complete: v${VERSION}"
echo "  App folder : $APP_DIR"
echo "  Binary     : $BIN"
echo "  Tarball    : $TARBALL"
ls -lh "$BIN" "$TARBALL" 2>/dev/null || true

mkdir -p "$ROOT/build"
echo "==> Smoke-test frozen binary (offscreen, 5s)"
if QT_QPA_PLATFORM=offscreen QTWEBENGINE_DISABLE_SANDBOX=1 \
  timeout 5 "$BIN" >"$ROOT/build/frozen_smoke.log" 2>&1; then
  echo "WARNING: binary exited before timeout — see build/frozen_smoke.log"
  tail -30 "$ROOT/build/frozen_smoke.log" || true
else
  code=$?
  if [[ $code -eq 124 ]]; then
    echo "Smoke OK: binary stayed running under offscreen Qt"
  else
    echo "WARNING: smoke exit code $code — see build/frozen_smoke.log"
    tail -40 "$ROOT/build/frozen_smoke.log" || true
  fi
fi
