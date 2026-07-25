#!/usr/bin/env bash
# Build (unless --skip-build) and install the app for this user on Nobara/Linux.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

INSTALL_DIR="${MOVER_INSTALL_DIR:-$HOME/.local/share/mover-sis-monitor}"
SKIP_BUILD=0
if [[ "${1:-}" == "--skip-build" ]]; then
  SKIP_BUILD=1
fi

if [[ ! -f "$ROOT/.venv/bin/activate" ]]; then
  python3 -m venv "$ROOT/.venv"
fi
# shellcheck disable=SC1091
source "$ROOT/.venv/bin/activate"
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"

VERSION="$(tr -d '[:space:]' < "$ROOT/VERSION")"
echo "==> Installing MOVER SIS Monitor v${VERSION} → $INSTALL_DIR"

if [[ "$SKIP_BUILD" -eq 0 ]]; then
  "$ROOT/scripts/build_executable.sh"
fi

APP_SRC="$ROOT/dist/MOVER-SIS-Monitor"
if [[ ! -x "$APP_SRC/MOVER-SIS-Monitor" ]]; then
  echo "ERROR: missing built binary at $APP_SRC/MOVER-SIS-Monitor" >&2
  echo "Run ./scripts/build_executable.sh first." >&2
  exit 1
fi

# Preserve user data links if present
KEEP_EMR=""
KEEP_PROC=""
if [[ -L "$INSTALL_DIR/data/raw/EMR" ]]; then
  KEEP_EMR="$(readlink -f "$INSTALL_DIR/data/raw/EMR" || true)"
fi
if [[ -L "$INSTALL_DIR/data/processed" ]]; then
  KEEP_PROC="$(readlink -f "$INSTALL_DIR/data/processed" || true)"
fi

rm -rf "$INSTALL_DIR"
mkdir -p "$INSTALL_DIR"
cp -a "$APP_SRC/." "$INSTALL_DIR/"
chmod +x "$INSTALL_DIR/MOVER-SIS-Monitor" "$INSTALL_DIR/launch.sh" 2>/dev/null || true

# Restore / create data links to project by default
mkdir -p "$INSTALL_DIR/data/raw"
# Remove placeholder dirs from the build so ln can create symlinks
rm -rf "$INSTALL_DIR/data/raw/EMR" "$INSTALL_DIR/data/processed"
if [[ -n "$KEEP_EMR" && -d "$KEEP_EMR" ]]; then
  ln -sfn "$KEEP_EMR" "$INSTALL_DIR/data/raw/EMR"
elif [[ -d "$ROOT/data/raw/EMR" ]]; then
  ln -sfn "$ROOT/data/raw/EMR" "$INSTALL_DIR/data/raw/EMR"
else
  mkdir -p "$INSTALL_DIR/data/raw/EMR"
fi
if [[ -n "$KEEP_PROC" && -d "$KEEP_PROC" ]]; then
  ln -sfn "$KEEP_PROC" "$INSTALL_DIR/data/processed"
elif [[ -d "$ROOT/data/processed" ]]; then
  ln -sfn "$ROOT/data/processed" "$INSTALL_DIR/data/processed"
else
  mkdir -p "$INSTALL_DIR/data/processed"
fi

# User CLI + desktop entry → installed frozen app (versioned)
mkdir -p "$HOME/.local/bin" "$HOME/.local/share/applications"
cat > "$HOME/.local/bin/mover-sis-monitor" <<EOF
#!/usr/bin/env bash
exec "$INSTALL_DIR/launch.sh" "\$@"
EOF
chmod +x "$HOME/.local/bin/mover-sis-monitor"

cat > "$HOME/.local/share/applications/mover-sis-monitor.desktop" <<EOF
[Desktop Entry]
Type=Application
Version=${VERSION}
Name=MOVER SIS Monitor
GenericName=Ventilation & Anesthesia Monitor
Comment=MOVER SIS research dashboard v${VERSION}
Exec=$INSTALL_DIR/launch.sh
Path=$INSTALL_DIR
Icon=applications-science
Terminal=false
Categories=Science;Education;MedicalSoftware;
StartupNotify=true
StartupWMClass=MOVER-SIS-Monitor
Keywords=anesthesia;ventilation;MOVER;SIS;
EOF
update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true

echo
echo "Installed v${VERSION}"
echo "  App      : $INSTALL_DIR"
echo "  Version  : $(cat "$INSTALL_DIR/VERSION" 2>/dev/null || echo unknown)"
echo "  CLI      : mover-sis-monitor"
echo "  Menu     : MOVER SIS Monitor"
echo "  EMR link : $(readlink -f "$INSTALL_DIR/data/raw/EMR" 2>/dev/null || echo none)"
echo
echo "Launch: mover-sis-monitor"
