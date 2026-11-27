"""The Settings row that converts raw Epic records once: shown only for Epic, converts, stops and resumes."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from src.desktop.settings_page import SettingsPage
from src.pipeline.epic_import import read_import_marker
from src.runtime_paths import clear_session_path_overrides, set_session_paths
from tests.test_epic_import import _epic


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def settings(monkeypatch, tmp_path, synthetic_emr):
    monkeypatch.setenv("MOVER_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setattr("src.llm.service.ensure_ollama", lambda **kw: SimpleNamespace(models=[]))
    clear_session_path_overrides()
    set_session_paths(emr_dir=synthetic_emr, processed_dir=tmp_path / "processed")
    yield
    clear_session_path_overrides()


def _wait_until(done, ms=15000):
    loop = QEventLoop()
    timer = QTimer()
    timer.timeout.connect(lambda: loop.quit() if done() else None)
    timer.start(20)
    QTimer.singleShot(ms, loop.quit)
    loop.exec()
    timer.stop()


def test_the_row_shows_only_for_raw_epic_records(qapp, tmp_path, synthetic_emr):
    page = SettingsPage()
    page.row_emr.set_text(str(synthetic_emr))
    assert page.row_epic.isHidden()
    root = _epic(tmp_path)
    page.row_emr.set_text(str(root / "emr" / "EPIC_EMR" / "EMR"))
    assert not page.row_epic.isHidden()
    assert page.row_epic.out_edit.text() == str(root.with_name("mover-epic-sis"))
    assert page.row_epic.convert_button.text() == "Convert"
    assert "These are MOVER Epic records" in page.row_emr.note.text()


def test_convert_fills_the_emr_and_processed_folders(qapp, tmp_path):
    page = SettingsPage()
    root = _epic(tmp_path)
    page.row_emr.set_text(str(root))
    out = tmp_path / "converted"
    page.row_epic.out_edit.setText(str(out))
    page.row_epic.convert_button.click()
    # Wait for the row's own reaction, not just the thread: its last signal is delivered after the thread ends.
    _wait_until(lambda: page.row_emr.text() == str(out))
    assert read_import_marker(out)["cases"] == 2
    assert page.row_emr.text() == str(out)
    assert page.row_emr.note.text().startswith("✓ Epic records (converted)")
    # Its own processed folder beside it, never a "processed" folder an SIS EMR beside it might use.
    assert page.row_processed.text() == str(tmp_path / "converted-processed")
    assert "✓ Converted" in page.row_epic.note.text()


def test_stop_then_continue_resumes_the_conversion(qapp, tmp_path, monkeypatch):
    page = SettingsPage()
    root = _epic(tmp_path)
    page.row_emr.set_text(str(root))
    out = tmp_path / "converted"
    page.row_epic.out_edit.setText(str(out))
    # Stop as soon as the first flowsheet part is done.
    import src.pipeline.epic_import as ei

    real = ei._pass1_part

    def one_part_then_stop(*args, **kwargs):
        kept = real(*args, **kwargs)
        page.row_epic._worker.request_stop()  # this runs on the worker thread: no widget calls here
        return kept

    monkeypatch.setattr(ei, "_pass1_part", one_part_then_stop)
    page.row_epic.convert_button.click()
    _wait_until(lambda: not page.row_epic.running() and page.row_epic.convert_button.text() == "Continue")
    assert page.row_epic.convert_button.text() == "Continue" and read_import_marker(out) is None
    monkeypatch.setattr(ei, "_pass1_part", real)
    page.row_epic.convert_button.click()
    _wait_until(lambda: page.row_emr.text() == str(out))
    assert read_import_marker(out)["finished"] and page.row_emr.text() == str(out)
