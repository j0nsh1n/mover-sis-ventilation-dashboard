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

# Heavy GUI / plotting stacks (shiboken6 is required at runtime for PySide6)
pyside_datas, pyside_binaries, pyside_hidden = collect_all("PySide6")
shiboken_datas, shiboken_binaries, shiboken_hidden = collect_all("shiboken6")
# Plotly intentionally omitted from desktop binary (matplotlib charts only)
plotly_datas, plotly_binaries, plotly_hidden = [], [], []
numpy_datas, numpy_binaries, numpy_hidden = collect_all("numpy")
scipy_datas, scipy_binaries, scipy_hidden = collect_all("scipy")
try:
    mpl_datas, mpl_binaries, mpl_hidden = collect_all("matplotlib")
except Exception:
    mpl_datas, mpl_binaries, mpl_hidden = [], [], []
try:
    pil_datas, pil_binaries, pil_hidden = collect_all("PIL")
except Exception:
    try:
        pil_datas, pil_binaries, pil_hidden = collect_all("Pillow")
    except Exception:
        pil_datas, pil_binaries, pil_hidden = [], [], []

# Optional but commonly pulled
try:
    pd_datas, pd_binaries, pd_hidden = collect_all("pandas")
except Exception:
    pd_datas, pd_binaries, pd_hidden = [], [], []

try:
    pa_datas, pa_binaries, pa_hidden = collect_all("pyarrow")
except Exception:
    pa_datas, pa_binaries, pa_hidden = [], [], []

datas = [
    (str(root / "src" / "config" / "thresholds.yaml"), "src/config"),
    (str(root / "VERSION"), "."),
    (str(root / "data" / "README.md"), "data"),
]
datas += (
    pyside_datas
    + shiboken_datas
    + plotly_datas
    + numpy_datas
    + scipy_datas
    + mpl_datas
    + pil_datas
    + pd_datas
    + pa_datas
)
datas += collect_data_files("src", includes=["**/*.yaml"])

binaries = (
    pyside_binaries
    + shiboken_binaries
    + plotly_binaries
    + numpy_binaries
    + scipy_binaries
    + mpl_binaries
    + pil_binaries
    + pd_binaries
    + pa_binaries
)

# Explicitly ship shiboken + numpy/scipy wheel .libs (Nobara/Fedora layouts)
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
    (str(_site / "PySide6" / "libpyside6*.so*"), "PySide6"),
    (str(_site / "numpy.libs" / "*"), "numpy.libs"),
    (str(_site / "scipy.libs" / "*"), "scipy.libs"),
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

hiddenimports = sorted(
    set(
        pyside_hidden
        + shiboken_hidden
        + plotly_hidden
        + numpy_hidden
        + scipy_hidden
        + mpl_hidden
        + pil_hidden
        + pd_hidden
        + pa_hidden
        + collect_submodules("src")
        + [
            "shiboken6",
            "shiboken6.Shiboken",
            "PySide6.QtCore",
            "PySide6.QtGui",
            "PySide6.QtWidgets",
            "PySide6.QtNetwork",
            "matplotlib",
            "matplotlib.pyplot",
            "PIL",
            "PIL.Image",
            "yaml",
            "pyarrow",
            "pandas",
            "numpy",
            "scipy",
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
        "test",
        "tests",
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
