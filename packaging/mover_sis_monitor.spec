# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for Nobara / Linux x86_64 desktop executable (onedir).
#
# Build:
#   ./scripts/build_executable.sh
#
# Output:
#   dist/MOVER-SIS-Monitor/MOVER-SIS-Monitor

from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

block_cipher = None
root = Path(SPECPATH).resolve().parent

# The desktop app imports only PySide6.QtCore / QtGui / QtWidgets (no QtWebEngine,
# QtQml/QtQuick, Qt3D, QtCharts, QtSvg, QtNetwork, ...) and no scipy or plotly, so
# nothing here uses collect_all(). PyInstaller's per-module hooks then bundle just
# the Qt libraries and plugins that QtCore/QtGui/QtWidgets need, and the
# numpy / pandas / matplotlib / pyarrow / PIL hooks bundle their runtime files.
# Test suites and headers are dropped by _is_unneeded() below (path based, so it
# is a no-op wherever a pattern does not match, e.g. on Windows).
# shiboken6 is required at runtime for PySide6; it is small, so collect it whole.
shiboken_datas, shiboken_binaries, shiboken_hidden = collect_all("shiboken6")

datas = [
    (str(root / "src" / "config" / "thresholds.yaml"), "src/config"),
    (str(root / "VERSION"), "."),
    (str(root / "data" / "README.md"), "data"),
]
datas += shiboken_datas
datas += collect_data_files("src", includes=["**/*.yaml"])

binaries = list(shiboken_binaries)

# Explicitly ship shiboken + numpy/pillow/matplotlib wheel .libs (Nobara/Fedora layouts)
import glob as _glob
import os as _os
import sys as _sys

_site_candidates = [
    Path(_sys.prefix)
    / "lib"
    / f"python{_sys.version_info.major}.{_sys.version_info.minor}"
    / "site-packages",
    Path(_sys.prefix)
    / "lib64"
    / f"python{_sys.version_info.major}.{_sys.version_info.minor}"
    / "site-packages",
]
_site = next((p for p in _site_candidates if p.is_dir()), _site_candidates[0])

for _pattern, _dest in (
    (str(_site / "shiboken6" / "libshiboken6*.so*"), "shiboken6"),
    (str(_site / "shiboken6" / "Shiboken.abi3.so"), "shiboken6"),
    (str(_site / "PySide6" / "libpyside6.abi3.so*"), "PySide6"),
    (str(_site / "numpy.libs" / "*"), "numpy.libs"),
    (str(_site / "pandas.libs" / "*"), "pandas.libs"),
    (str(_site / "pillow.libs" / "*"), "pillow.libs"),
    (str(_site / "PIL.libs" / "*"), "PIL.libs"),
    (str(_site / "matplotlib.libs" / "*"), "matplotlib.libs"),
    (str(_site / "kiwisolver.libs" / "*"), "kiwisolver.libs"),
    (str(_site / "pyarrow.libs" / "*"), "."),
):
    for _lib in _glob.glob(_pattern):
        if _os.path.isfile(_lib):
            binaries.append((_lib, _dest))

# Qt 6.5+ dlopen()s libxcb-cursor at runtime, so dependency analysis misses it
# and hosts without it (stock Ubuntu) fail with "could not load the Qt platform
# plugin xcb". Ship the build host's copy next to the other bundled libxcb libs.
for _dir in ("/usr/lib/x86_64-linux-gnu", "/usr/lib64", "/usr/lib"):
    _cursor = Path(_dir) / "libxcb-cursor.so.0"
    if _cursor.is_file():
        binaries.append((str(_cursor), "."))
        break

hiddenimports = sorted(
    set(
        shiboken_hidden
        + collect_submodules("src")
        + [
            "shiboken6",
            "shiboken6.Shiboken",
            "PySide6.QtCore",
            "PySide6.QtGui",
            "PySide6.QtWidgets",
            "matplotlib",
            "matplotlib.pyplot",
            "matplotlib.backends.backend_agg",
            "PIL",
            "PIL.Image",
            "yaml",
            "pyarrow",
            "pyarrow.parquet",
            "pandas",
            "numpy",
        ]
    )
)

a = Analysis(
    [str(root / "packaging" / "entrypoint.py")],
    pathex=[str(root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[
        str(root / "packaging" / "rthook_paths.py"),
        str(root / "packaging" / "rthook_qt.py"),
    ],
    excludes=[
        "tkinter",
        "IPython",
        "jupyter",
        "notebook",
        "streamlit",
        "tornado",
        "plotly",
        "scipy",
        "test",
        "tests",
        "pandas.tests",
        "numpy.tests",
        "matplotlib.tests",
        "pyarrow.tests",
        "pyarrow.flight",
        "PySide6.QtNetwork",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtWebEngineQuick",
        "PySide6.QtQml",
        "PySide6.QtQuick",
        "PySide6.QtQuickWidgets",
        "PySide6.QtQuick3D",
        "PySide6.Qt3DCore",
        "PySide6.QtCharts",
        "PySide6.QtMultimedia",
        "PySide6.QtSql",
        "PySide6.QtSvg",
        "PySide6.QtDesigner",
        "PySide6.QtHelp",
        "PySide6.QtPdf",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)


def _is_conflicting_system_qt(src_path: str) -> bool:
    """Drop system Qt libs that clash with PySide6's private Qt build."""
    p = str(src_path)
    name = _os.path.basename(p)
    if "PySide6" in p or "shiboken6" in p:
        return False
    if name.startswith("libQt6") or name.startswith("libQt5"):
        # Prefer the copies under PySide6/Qt/lib only
        return True
    return False


a.binaries = [b for b in a.binaries if not _is_conflicting_system_qt(b[1])]


_TEST_PACKAGES = {"numpy", "pandas", "matplotlib", "pyarrow", "PIL", "yaml"}
_EMBEDDED_PLATFORMS = {"qeglfs", "qlinuxfb", "qvnc", "qvkkhrdisplay", "qminimalegl"}
# Qt libraries that only the dropped plugins (virtual keyboard, PDF image
# format, EGLFS) linked against; nothing else in the bundle needs them.
_UNUSED_QT_LIBS = (
    "Qt6VirtualKeyboard",
    "Qt6Pdf",
    "Qt6Qml",
    "Qt6Quick",
    "Qt6EglFS",
    "Qt6EglFsKms",
)


def _stem(name: str) -> str:
    """'libQt6Qml.so.6' -> 'Qt6Qml'; 'Qt6Qml.dll' -> 'Qt6Qml'; 'qxcb.so' -> 'qxcb'."""
    base = name[3:] if name.startswith("lib") else name
    return base.split(".", 1)[0]


def _is_unneeded(dest: str) -> bool:
    """Path-based filter for files the desktop app never loads.

    ``dest`` is the destination path inside the bundle. Patterns are matched on
    forward-slash-normalised path segments, so the same rules apply on Windows.
    """
    parts = dest.replace("\\", "/").split("/")
    if parts[0] in _TEST_PACKAGES and ("tests" in parts or "test" in parts):
        return True
    # C/C++ headers shipped inside wheels
    if parts[0] in {"pyarrow", "numpy"} and "include" in parts:
        return True
    # matplotlib sample data is for gallery examples only
    if parts[:3] == ["matplotlib", "mpl-data", "sample_data"]:
        return True
    stem = _stem(parts[-1])
    # pyarrow Flight (RPC) is never imported by pandas.read_parquet / to_parquet
    if stem.startswith(("arrow_flight", "arrow_python_flight")) or (
        parts[0] == "pyarrow" and stem.startswith("_flight")
    ):
        return True
    # Qt libraries that only the dropped plugins pull in. Dependency analysis
    # may also place them at the bundle top level, so match on the file name.
    if "plugins" not in parts and stem.startswith(_UNUSED_QT_LIBS):
        return True
    if parts[0] == "PySide6":
        # Qt is not translated by the app (no QTranslator), so the .qm files are dead weight
        if "translations" in parts:
            return True
        if "plugins" in parts:
            # Embedded-only platforms/inputs and the on-screen keyboard; xcb,
            # wayland, offscreen and minimal are kept.
            if parts[-2] in {"egldeviceintegrations", "generic"}:
                return True
            if parts[-2] == "platforms" and stem in _EMBEDDED_PLATFORMS:
                return True
            if stem in {"qtvirtualkeyboardplugin", "qpdf"}:
                return True
    return False


a.binaries = [b for b in a.binaries if not _is_unneeded(b[0])]
a.datas = [d for d in a.datas if not _is_unneeded(d[0])]

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MOVER-SIS-Monitor",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # GUI app — no terminal
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="MOVER-SIS-Monitor",
)
