#!/usr/bin/env bash
# Install a local .desktop launcher for the current user (Linux).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
mkdir -p "$APP_DIR"

LAUNCHER="$ROOT/scripts/run_desktop.sh"
chmod +x "$LAUNCHER"

ENTRY="$APP_DIR/mover-sis-ventilation-monitor.desktop"
cat > "$ENTRY" <<EOF
[Desktop Entry]
Type=Application
Name=MOVER SIS Ventilation Monitor
Comment=Intraoperative ventilation & anesthesia research dashboard
Exec=$LAUNCHER
Path=$ROOT
Terminal=false
Categories=Science;Education;MedicalSoftware;
StartupNotify=true
EOF

chmod +x "$ENTRY"
echo "Installed: $ENTRY"
echo "You can launch \"MOVER SIS Ventilation Monitor\" from your app menu."
echo "Or run: $LAUNCHER"
