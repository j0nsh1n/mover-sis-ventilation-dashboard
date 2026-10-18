# Build and run the Linux AppImage

Build the existing frozen Linux bundle, then package it with:

```bash
APPIMAGETOOL=/path/to/appimagetool-x86_64.AppImage \
APPIMAGE_RUNTIME=/path/to/runtime-x86_64 \
./scripts/build_appimage.sh
```

Add `--skip-build` to package an existing matching bundle. The script refuses
to replace an existing candidate. It copies only the executable, bundled
libraries, launcher and version file, excluding research files beside the app.
It adds the AppImage launcher, desktop entry and icon. The result and SHA256
file appear under `dist/`.

The packaging tools come from the official AppImage releases. This local
candidate uses these SHA256 pins:

- appimagetool-x86_64.AppImage, continuous release published 2026-10-04:
  `95cbe7cce9717fce90c484e34052ee7c7f1d7635b33c12525b4776826a7d29b6`.
- runtime-x86_64, continuous release published 2026-09-28, commit 8f39b89:
  `156f4bdbde9c52d01814600013e0a273f0118dc2de98975f3c8c63427ec79074`.

The script requires the runtime explicitly, so appimagetool does not silently
download a newer runtime. No packaging tool is bundled as a Python dependency.

Make the output executable and launch it:

```bash
chmod +x MOVER-SIS-Monitor-v0.15.0-x86_64.AppImage
./MOVER-SIS-Monitor-v0.15.0-x86_64.AppImage
```

Settings retain their existing location. Without saved or explicit data paths,
the AppImage uses `$XDG_DATA_HOME/mover-sis-monitor/data`, or
`~/.local/share/mover-sis-monitor/data` when XDG_DATA_HOME is unset or empty. Saved paths
and MOVER_DATA_DIR, MOVER_EMR_DIR and MOVER_PROCESSED_DIR overrides still apply.
Research outputs are outside the read-only image.

This initial AppImage uses manual file replacement for updates. Its launcher
disables the directory-archive updater, which cannot replace a mounted image.
Existing unpacked Linux and Windows packages retain their updater behavior.

If FUSE mounting is unavailable, run with `--appimage-extract-and-run` and set
TMPDIR to a writable folder on disk. Desktop libraries required by the Qt
launcher are still required on the host. This candidate is verified on the
local Linux host; other distributions need their own launch check.

Upstream references:

- https://github.com/AppImage/appimagetool
- https://github.com/AppImage/type2-runtime
- https://docs.appimage.org/packaging-guide/manual.html
