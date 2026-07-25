"""Desktop UI: directory selection wiring (offscreen)."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")


def _qt_available() -> bool:
    try:
        from PySide6.QtWidgets import QApplication  # noqa: F401

        return True
    except ImportError:
        return False


@pytest.fixture(scope="module")
def qapp():
    if not _qt_available():
        pytest.skip("PySide6/Qt system libs unavailable (e.g. missing libEGL)")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        try:
            QApplication.setAttribute(
                Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True
            )
        except Exception:
            pass
        app = QApplication([])
    return app


@pytest.fixture(autouse=True)
def _isolate_paths(tmp_path, monkeypatch):
    from src.runtime_paths import clear_session_path_overrides, set_session_paths

    monkeypatch.setenv("MOVER_CONFIG_DIR", str(tmp_path / "cfg"))
    # Point processed at empty dir so MainWindow does not auto-load project cache
    empty_proc = tmp_path / "empty_processed"
    empty_proc.mkdir()
    clear_session_path_overrides()
    set_session_paths(processed_dir=empty_proc)
    # Skip deferred autoload races in unit tests unless a test enables it
    monkeypatch.setattr(
        "src.desktop.app.MainWindow._autoload_processed",
        lambda self: None,
    )
    yield
    clear_session_path_overrides()


def test_apply_data_folder_updates_labels(qapp, tmp_path):
    from src.desktop.app import MainWindow
    from src.runtime_paths import emr_dir, processed_dir

    emr = tmp_path / "mydata" / "EMR"
    emr.mkdir(parents=True)
    (emr / "patient_information.csv").write_text("PID\nz\n")
    (emr / "patient_ventilator.csv").write_text("PID\n")
    (emr / "patient_vitals.csv").write_text("PID\n")

    win = MainWindow()
    win.show()
    qapp.processEvents()
    win._apply_data_folder(tmp_path / "mydata")
    qapp.processEvents()

    assert emr_dir() == emr.resolve()
    assert "EMR" in win.lbl_emr_path.text()
    assert str(emr.resolve()) in win.lbl_emr_path.text()
    assert processed_dir().name == "processed"
    win.close()
    qapp.processEvents()


def test_apply_invalid_folder_shows_error_path(qapp, tmp_path, monkeypatch):
    from src.desktop.app import MainWindow

    # Avoid modal dialogs blocking tests
    monkeypatch.setattr(
        "src.desktop.app.QMessageBox.critical",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "src.desktop.app.QMessageBox.information",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "src.desktop.app.QMessageBox.warning",
        lambda *a, **k: None,
    )

    empty = tmp_path / "nope"
    empty.mkdir()
    win = MainWindow()
    win.show()
    qapp.processEvents()
    before = win.lbl_emr_path.text()
    win._apply_data_folder(empty)
    qapp.processEvents()
    # Path labels unchanged on failure
    assert win.lbl_emr_path.text() == before
    win.close()


def test_pipeline_uses_selected_emr(qapp, synthetic_emr, tmp_path, monkeypatch):
    """Selecting synthetic EMR and running ensure_data via worker path."""
    from src.desktop.app import MainWindow
    from src.runtime_paths import configure_from_user_directory, processed_dir
    from src.services.data import ensure_data

    monkeypatch.setenv("MOVER_ALLOW_EXTERNAL_OUTPUT", "1")
    monkeypatch.setattr(
        "src.desktop.app.QMessageBox.information",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "src.desktop.app.QMessageBox.critical",
        lambda *a, **k: None,
    )

    win = MainWindow()
    win.show()
    # Point at synthetic EMR
    configure_from_user_directory(synthetic_emr, persist=True)
    win._sync_path_fields_from_runtime()

    out = tmp_path / "out_proc"
    # Direct ensure_data with selected dirs (same as worker)
    cases, ts, flags, episodes, events = ensure_data(
        pids=["caseA", "caseB"],
        force=True,
        emr_dir=synthetic_emr,
        processed_dir=out,
        min_vent_rows=5,
    )
    assert len(cases) == 2
    assert (out / "cases.parquet").is_file()
    win.close()
