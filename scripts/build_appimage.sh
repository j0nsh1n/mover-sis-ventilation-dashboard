#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
if (( $# > 1 )) || [[ "${1:-}" != "" && "${1:-}" != --skip-build ]]; then
  echo "Usage: $0 [--skip-build]" >&2
  exit 2
fi

TOOL="${APPIMAGETOOL:-appimagetool}"
if ! command -v "$TOOL" >/dev/null 2>&1; then
  echo "Set APPIMAGETOOL to the appimagetool executable." >&2
  exit 1
fi
if [[ ! -f "${APPIMAGE_RUNTIME:-}" ]]; then
  echo "Set APPIMAGE_RUNTIME to the downloaded x86_64 type-2 runtime." >&2
  exit 1
fi

VERSION="$(tr -d '[:space:]' < VERSION)"
OUTPUT="$ROOT/dist/MOVER-SIS-Monitor-v${VERSION}-x86_64.AppImage"
if [[ -e "$OUTPUT" ]]; then
  echo "Refusing to replace $OUTPUT. Move the previous candidate before rebuilding." >&2
  exit 1
fi
if [[ "${1:-}" != --skip-build ]]; then
  "$ROOT/scripts/build_executable.sh"
fi

BUNDLE="$ROOT/dist/MOVER-SIS-Monitor"
if [[ ! -x "$BUNDLE/MOVER-SIS-Monitor" || ! -x "$BUNDLE/launch.sh" || ! -d "$BUNDLE/_internal" ]]; then
  echo "Build the frozen application before using --skip-build." >&2
  exit 1
fi
if [[ ! -f "$BUNDLE/VERSION" ]] || [[ "$(tr -d '[:space:]' < "$BUNDLE/VERSION")" != "$VERSION" ]]; then
  echo "Frozen application version does not match VERSION; rebuild it first." >&2
  exit 1
fi

mkdir -p "$ROOT/build" "$ROOT/dist"
WORK="$(mktemp -d "$ROOT/build/appimage.XXXXXX")"
APPDIR="$WORK/MOVER-SIS-Monitor.AppDir"
mkdir "$APPDIR"
cp -a "$BUNDLE/MOVER-SIS-Monitor" "$BUNDLE/_internal" "$BUNDLE/launch.sh" "$BUNDLE/VERSION" "$APPDIR/"
cp "$ROOT/packaging/appimage/AppRun" "$ROOT/packaging/appimage/mover-sis-monitor.desktop" \
  "$ROOT/packaging/appimage/mover-sis-monitor.svg" "$APPDIR/"
chmod +x "$APPDIR/AppRun"
ln -s mover-sis-monitor.svg "$APPDIR/.DirIcon"

export ARCH=x86_64 VERSION
APPIMAGE_EXTRACT_AND_RUN=1 "$TOOL" --runtime-file "$APPIMAGE_RUNTIME" \
  --no-appstream --mksquashfs-opt=-processors --mksquashfs-opt=4 \
  "$APPDIR" "$WORK/MOVER-SIS-Monitor.AppImage"
chmod +x "$WORK/MOVER-SIS-Monitor.AppImage"
mv "$WORK/MOVER-SIS-Monitor.AppImage" "$OUTPUT"
(cd "$ROOT/dist" && sha256sum "${OUTPUT##*/}") > "$OUTPUT.sha256"
echo "AppImage ready: $OUTPUT"
