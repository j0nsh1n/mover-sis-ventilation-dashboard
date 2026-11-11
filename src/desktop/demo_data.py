"""One-click fictional demo data, so the app can be tried without access to the MOVER dataset.

The data is made by the project's own synthetic generator into a folder that only this module
creates. A folder that already exists without this app's marker is never written to.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from src.runtime_paths import (
    DEMO_MARKER,
    app_dir,
    configure_emr_directory,
    configure_processed_directory,
    data_dir,
    demo_data_dir,
    emr_dir,
    processed_dir,
    read_demo_marker,
    set_session_paths,
)
from src.user_settings import load_settings, mark_setup_complete

DEMO_CASES = 30
DEMO_SEED = 7
STEPS = (
    "Writing the fictional EMR tables",
    "Analyzing the fictional cases with the rule preset",
    "Opening the demo data",
)


class DemoDataError(Exception):
    """A failure that can be shown as it is: the message is plain words with no patient data in it."""


def _marker(root: Path) -> Path:
    return root / DEMO_MARKER


def check_demo_folder(root: Path) -> None:
    """Raise ``DemoDataError`` unless ``root`` is new, empty, or a folder this app made."""
    if root.is_relative_to(app_dir().resolve()):
        raise DemoDataError(
            f"The demo data folder ({root}) is inside the program folder. Set MOVER_DEMO_DATA_DIR to a folder "
            "in your own data directory."
        )
    if root.exists() and not root.is_dir():
        raise DemoDataError(f"{root} exists and is not a folder, so demo data was not made there.")
    if root.is_dir() and read_demo_marker(root) is None and any(root.iterdir()):
        raise DemoDataError(
            f"A folder named {root} already exists and was not made by MOVER Suite, so it was left as it is. "
            "Move it away, or set MOVER_DEMO_DATA_DIR to another folder."
        )


def demo_is_complete(root: Path) -> bool:
    marker = read_demo_marker(root)
    return bool(marker and marker.get("complete") and (root / "processed" / "cases.parquet").is_file()
                and (root / "EMR" / "patient_information.csv").is_file())


def build_demo_data(root: Path, progress: Callable[[int], None] = lambda _step: None) -> Path:
    """Make the demo data in ``root`` (or reuse it when it is already complete) and return its EMR folder.

    Safe to run twice: a complete demo is reused, and an unfinished one is redone over its own files.
    """
    check_demo_folder(root)
    emr = root / "EMR"
    if demo_is_complete(root):
        progress(len(STEPS) - 1)
        return emr
    root.mkdir(parents=True, exist_ok=True)
    _marker(root).write_text(json.dumps({
        "made_by": "MOVER Suite", "what": "fictional demo data, not patient records", "complete": False,
    }), encoding="utf-8")
    from src.pipeline.run import run_pipeline
    from src.pipeline.synthetic import generate_synthetic_emr

    progress(0)
    generate_synthetic_emr(emr, n_cases=DEMO_CASES, seed=DEMO_SEED)
    progress(1)
    preset = str(load_settings().get("preset") or "default")
    # The pipeline writes only under folders the app has selected, so the demo folder is selected for the run
    # and the folders in use are put back if it fails.
    previous = {"data_dir": data_dir(), "emr_dir": emr_dir(), "processed_dir": processed_dir()}
    set_session_paths(data_dir=root, emr_dir=emr, processed_dir=root / "processed")
    try:
        run_pipeline(
            emr_dir=emr, output_dir=root / "processed", n_cases=DEMO_CASES,
            preset=preset if preset in {"default", "lenient", "strict"} else "default",
            write_sample_csv=False,
        )
    except BaseException:
        set_session_paths(**previous)
        raise
    _marker(root).write_text(json.dumps({
        "made_by": "MOVER Suite", "what": "fictional demo data, not patient records", "complete": True,
    }), encoding="utf-8")
    progress(2)
    return emr


def use_demo_data(root: Path) -> None:
    """Point the app at the demo folder and record that setup is done."""
    configure_emr_directory(root / "EMR", persist=True)
    # The EMR step keeps an older processed folder that has its own cache, which would belong to other data
    configure_processed_directory(root / "processed", persist=True)
    mark_setup_complete()


class DemoDataWorker(QThread):
    """Builds the demo data off the window's thread and reports named steps."""

    step = Signal(int)
    finished_ok = Signal(object)
    failed = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.root = demo_data_dir()

    def run(self) -> None:
        try:
            emr = build_demo_data(self.root, self.step.emit)
            self.finished_ok.emit(emr.parent)
        except DemoDataError as exc:
            self.failed.emit(str(exc), "")
        except Exception as exc:
            from src.desktop.errors import error_message

            # The exception text can hold paths; it goes to the log, and the person gets the code and log place.
            self.failed.emit("Demo data could not be made.", error_message("demo-data", exc))
