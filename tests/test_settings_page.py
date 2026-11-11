"""Settings is a page of the window: same saved values as the old dialog, nothing written until Save,
and leaving with edits asks first."""

from __future__ import annotations

import json
import os
import shutil
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from src.desktop import settings_page
from src.desktop.app import MainWindow
from src.desktop.settings_page import LEAVE_DISCARD, LEAVE_SAVE, LEAVE_STAY, SettingsPage
from src.guardrails.exceptions import PathSafetyError
from src.runtime_paths import apply_persisted_settings, clear_session_path_overrides, emr_dir, processed_dir
from src.user_settings import load_settings, settings_path


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path, synthetic_emr):
    """A settings file in today's format, written by hand, and an EMR that passes the checks."""
    config = tmp_path / "config"
    config.mkdir()
    models = tmp_path / "models"
    models.mkdir()
    monkeypatch.setenv("MOVER_CONFIG_DIR", str(config))
    monkeypatch.delenv("MOVER_EMR_DIR", raising=False)
    monkeypatch.delenv("MOVER_PROCESSED_DIR", raising=False)
    monkeypatch.delenv("MOVER_WAVE_DIR", raising=False)
    monkeypatch.setenv("OLLAMA_MODELS", str(models))
    monkeypatch.setattr("src.llm.service.ensure_ollama", lambda **kw: SimpleNamespace(models=["demo:3b", "nomic-embed-text"]))
    (config / "settings.json").write_text(json.dumps({
        "emr_dir": str(synthetic_emr),
        "processed_dir": str(tmp_path / "processed"),
        "ollama_models_dir": str(models),
        "ollama_model": "demo:3b",
        "ollama_embed_model": "nomic-embed-text",
        "theme": "dark",
        "preset": "strict",
        "setup_complete": True,
        "n_cases": 12,
    }), encoding="utf-8")
    clear_session_path_overrides()
    apply_persisted_settings()
    yield SimpleNamespace(emr=synthetic_emr, processed=tmp_path / "processed", models=models, config=config)
    clear_session_path_overrides()


@pytest.fixture
def page(qapp, dispose):
    widget = dispose(SettingsPage())
    widget.resize(1100, 700)
    widget.show()
    qapp.processEvents()
    yield widget
    widget.close()


def test_a_settings_file_in_todays_format_loads_into_every_field_unchanged(page, env):
    assert page.values() == {
        "emr_dir": str(env.emr), "wave_dir": "", "processed_dir": str(env.processed),
        "ollama_models_dir": str(env.models), "ollama_model": "demo:3b",
        "ollama_embed_model": "nomic-embed-text", "theme": "dark", "preset": "strict",
    }
    assert not page.is_dirty()
    assert page.save_button.isEnabled() is False


def test_saving_writes_the_same_keys_and_leaves_unknown_keys_alone(page, env):
    page.combo_theme.setCurrentIndex(page.combo_theme.findData("light"))
    page.combo_preset.setCurrentIndex(page.combo_preset.findData("lenient"))
    page.edit_embed.setText("other-embed")
    assert page.save() is True
    saved = json.loads(settings_path().read_text(encoding="utf-8"))
    assert saved["theme"] == "light" and saved["preset"] == "lenient" and saved["ollama_embed_model"] == "other-embed"
    assert saved["emr_dir"] == str(env.emr.resolve()) and saved["setup_complete"] is True
    assert saved["n_cases"] == 12
    assert set(saved) <= {"data_dir", "emr_dir", "processed_dir", "wave_dir", "ollama_models_dir", "ollama_model",
                          "ollama_embed_model", "theme", "setup_complete", "n_cases", "preset"}
    assert not page.is_dirty()
    assert "theme" in page.status.text() and page.status.text().startswith("Saved:")


def test_nothing_is_written_until_save(page, env):
    before = settings_path().read_bytes()
    page.combo_theme.setCurrentIndex(page.combo_theme.findData("light"))
    page.row_processed.edit.setText(str(env.processed / "elsewhere"))
    page.combo_preset.setCurrentIndex(page.combo_preset.findData("default"))
    assert settings_path().read_bytes() == before
    assert page.changes() == ["processed folder", "rule preset", "theme"]
    assert page.status.text() == "<b>3 unsaved changes:</b> processed folder, rule preset, theme"
    assert page.save_button.isEnabled() and page.discard_button.isEnabled()


def test_discard_restores_every_field_and_writes_nothing(page, env):
    before = settings_path().read_bytes()
    page.row_emr.edit.setText("/nowhere")
    page.combo_theme.setCurrentIndex(page.combo_theme.findData("light"))
    page.discard()
    assert page.values()["emr_dir"] == str(env.emr) and page.values()["theme"] == "dark"
    assert not page.is_dirty() and settings_path().read_bytes() == before
    assert page.status.text() == "Changes discarded. Settings are as last saved."


def test_a_refused_save_says_why_keeps_the_edits_and_writes_nothing(page, env):
    before = settings_path().read_bytes()
    page.combo_theme.setCurrentIndex(page.combo_theme.findData("light"))
    page.row_emr.edit.setText(str(env.models))  # a folder with no EMR tables
    assert page.save() is False
    assert settings_path().read_bytes() == before, "a refused save must not write the other values either"
    assert page.values()["theme"] == "light" and page.values()["emr_dir"] == str(env.models)
    assert page.is_dirty()
    text = page.status.text()
    assert text.startswith("Not saved. EMR:") and "Your edits are still here." in text


def test_processed_folder_inside_the_emr_is_refused_before_anything_is_written(page, env):
    before = settings_path().read_bytes()
    page.row_processed.edit.setText(str(env.emr / "processed"))
    assert page.save() is False
    assert "Processed output cannot be the EMR folder or inside it" in page.status.text()
    assert settings_path().read_bytes() == before


@pytest.fixture
def read_only_folder(tmp_path):
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    yield locked
    locked.chmod(0o700)


@pytest.mark.skipif(os.geteuid() == 0, reason="root can write into a read-only folder")
def test_a_save_that_cannot_make_the_processed_folder_writes_nothing_and_says_which_folder(
        page, env, read_only_folder, tmp_path):
    other_emr = tmp_path / "emr-two"
    shutil.copytree(env.emr, other_emr)
    before = settings_path().read_bytes()
    page.row_emr.edit.setText(str(other_emr))
    page.row_processed.edit.setText(str(read_only_folder / "sub" / "processed"))
    assert page.save() is False
    assert settings_path().read_bytes() == before, "the changed EMR folder must not be written either"
    assert emr_dir() == env.emr.resolve()
    assert page.status.text().startswith("Not saved. Processed:") and "cannot be written to" in page.status.text()
    assert page.status.property("state") == "problem"
    assert page.changes() == ["EMR folder", "processed folder"] and page.save_button.isEnabled()
    assert not (read_only_folder / "sub").exists()


@pytest.mark.parametrize("failure", [PermissionError(13, "Permission denied", "/somewhere/processed"),
                                     PathSafetyError("that place is not allowed")], ids=["OSError", "PathSafetyError"])
def test_a_failure_that_only_shows_during_the_writes_puts_the_old_file_back_byte_for_byte(
        page, env, tmp_path, monkeypatch, failure):
    other_emr = tmp_path / "emr-two"
    shutil.copytree(env.emr, other_emr)
    before = settings_path().read_bytes()

    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr("src.desktop.settings_ui.configure_processed_directory", fail)
    page.row_emr.edit.setText(str(other_emr))
    page.combo_preset.setCurrentIndex(page.combo_preset.findData("lenient"))
    assert page.save() is False, "the error must become a refusal, not leave the handler"
    assert settings_path().read_bytes() == before
    assert emr_dir() == env.emr.resolve() and processed_dir() == env.processed.resolve()
    text = page.status.text()
    assert text.startswith("Not saved. The settings could not be written:") and "Your edits are still here." in text
    assert page.changes() == ["EMR folder", "rule preset"] and page.save_button.isEnabled()


def test_an_os_error_that_reaches_the_handler_is_still_a_refusal_not_a_crash(page, env, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(settings_page, "apply_values", fail)
    page.combo_preset.setCurrentIndex(page.combo_preset.findData("lenient"))
    assert page.save() is False
    assert page.status.text().startswith("Not saved.") and "Your edits are still here." in page.status.text()
    assert page.is_dirty() and page.save_button.isEnabled()


def test_each_folder_row_checks_what_it_holds_in_a_sentence(page, env):
    assert "3 of 3 required tables found" in page.row_emr.note.text()
    assert page.row_emr.note.text().startswith("✓ ")
    page.row_emr.set_text(str(env.models))
    assert page.row_emr.note.text().startswith("✕ ") and "Not usable" in page.row_emr.note.text()
    assert "Nothing analyzed here yet" in page.row_processed.note.text()
    assert page.row_wave.note.text() == "Nothing to check."


def test_the_full_paths_in_use_show_whole(page, env):
    text = page.in_use_paths.text()
    assert str(env.emr) in text and str(env.processed) in text and "Wave: not set" in text


@pytest.mark.parametrize("choice,leaves,kept", [(LEAVE_STAY, False, True), (LEAVE_DISCARD, True, False),
                                                  (LEAVE_SAVE, True, False)])
def test_leaving_with_edits_asks_and_stay_keeps_every_edit(page, monkeypatch, choice, leaves, kept):
    asked = []
    monkeypatch.setattr(SettingsPage, "_ask_leave", lambda self: asked.append(self.changes()) or choice)
    page.combo_theme.setCurrentIndex(page.combo_theme.findData("light"))
    page.row_wave.edit.setText("")
    assert page.confirm_leave() is leaves
    assert asked == [["theme"]]
    assert page.is_dirty() is kept
    on_disk = load_settings()["theme"]
    assert on_disk == ("light" if choice == LEAVE_SAVE else "dark")
    if choice == LEAVE_STAY:
        assert page.values()["theme"] == "light"


def test_leaving_a_clean_page_does_not_ask(page, monkeypatch):
    monkeypatch.setattr(SettingsPage, "_ask_leave", lambda self: pytest.fail("asked on a clean page"))
    assert page.confirm_leave() is True


def test_the_leave_prompt_offers_save_discard_and_stay_in_plain_words(page, qapp, monkeypatch):
    seen = {}

    def inspect(box):
        seen["text"] = box.text()
        seen["buttons"] = [button.text() for button in box.buttons()]
        seen["more"] = box.informativeText()
        box.show()
        qapp.processEvents()
        # the prompt shows every button whole
        seen["wide_enough"] = all(button.width() >= button.fontMetrics().horizontalAdvance(button.text())
                                  for button in box.buttons())
        seen["default"] = box.defaultButton().text()
        return 0

    monkeypatch.setattr(settings_page.QMessageBox, "exec", inspect)
    page.combo_theme.setCurrentIndex(page.combo_theme.findData("light"))
    assert page.confirm_leave() is False  # no button clicked: counts as stay
    assert seen["text"] == "Save your changes to Settings?"
    assert sorted(seen["buttons"]) == ["Discard", "Save", "Stay"]
    assert "theme" in seen["more"] and seen["wide_enough"] and seen["default"] == "Stay"


def test_escape_asks_the_window_to_go_back(page, qapp):
    asked = []
    page.close_requested.connect(lambda: asked.append(True))
    page.sections.setFocus()
    QTest.keyClick(page.sections, Qt.Key.Key_Escape)
    page.row_emr.edit.setFocus()
    QTest.keyClick(page.row_emr.edit, Qt.Key.Key_Escape)
    assert asked == [True, True]


def test_sections_and_tab_order_follow_the_screen(page, qapp):
    assert [page.sections.item(i).text() for i in range(page.sections.count())] == [
        "Data sources", "Local AI", "Appearance", "Keyboard", "About"]
    seen = []
    page.sections.setFocus()
    for _ in range(12):
        qapp.processEvents()
        widget = qapp.focusWidget()
        seen.append(widget)
        QTest.keyClick(widget, Qt.Key.Key_Tab)
    names = [w.accessibleName() or w.text() for w in seen if w is not None]
    assert names[0] == "Settings sections"
    assert names[1:5] == ["EMR folder", "Choose emr folder", "Processed folder", "Choose processed folder"]
    for widget in seen:
        assert widget is not None and (widget.accessibleName() or getattr(widget, "text", lambda: "")())


def test_every_setting_the_dialog_had_is_on_the_page(page):
    page.show_section("Local AI")
    assert page.row_models.edit.accessibleName() == "Models directory"
    assert page.combo_model.isEditable() and page.combo_model.currentText() == "demo:3b"
    assert page.edit_embed.text() == "nomic-embed-text"
    assert page.embed_ready.note.text() == "Embedding model ready"
    assert [page.combo_preset.itemText(i) for i in range(3)] == ["Default", "Lenient", "Strict"]
    assert [page.combo_theme.itemData(i) for i in range(3)] == ["light", "dark", "system"]
    assert page.row_emr.edit.accessibleName() == "EMR folder"
    assert page.row_processed.edit.accessibleName() == "Processed folder"
    assert page.row_wave.edit.accessibleName() == "Wave folder"


@pytest.mark.parametrize("saved,expected", [("default", "default"), ("lenient", "lenient"), ("strict", "strict"),
                                            ("unknown", "default"), (None, "default")])
def test_the_page_loads_the_saved_preset(qapp, env, saved, expected, dispose):
    from src.user_settings import update_settings

    update_settings(preset=saved)
    widget = dispose(SettingsPage())
    assert widget.combo_preset.currentData() == expected
    widget.close()


# ----- in the window -----
@pytest.fixture
def setup_calls(monkeypatch):
    """Calls of data setup. The window binds its handlers while it is built, so this patches first."""
    calls: list[bool] = []
    monkeypatch.setattr(MainWindow, "_open_setup_wizard", lambda self, first_run=False: calls.append(first_run))
    return calls


@pytest.fixture
def window(qapp, monkeypatch, setup_calls, dispose):
    monkeypatch.setattr(MainWindow, "_startup", lambda self: None)
    monkeypatch.setattr(MainWindow, "_autoload_processed", lambda self: None)
    monkeypatch.setattr(MainWindow, "_refresh_ollama_models", lambda self, **kw: None)
    win = dispose(MainWindow())
    win.resize(1380, 860)
    win.show()
    qapp.processEvents()
    yield win
    win._suite_pages.get("settings") and win._suite_pages["settings"].discard()
    win.close()
    qapp.processEvents()


def test_settings_opens_in_the_window_from_the_rail_the_shortcut_and_the_palette(window, qapp):
    window.rail.button("settings").click()
    qapp.processEvents()
    assert window.module_stack.currentWidget() is window._suite_pages["settings"]
    assert window.head.crumb_text() == "Settings / Data sources"
    assert window.windowTitle().startswith("Settings — ")
    assert window.rail.button("settings").isChecked()
    window._close_settings()
    assert window.module_stack.currentWidget() is window.research
    assert not window.rail.button("settings").isChecked()
    shortcut = QKeySequence("Ctrl+,")
    assert window.settings_act.shortcut() == shortcut
    window.settings_act.trigger()
    assert window.module_stack.currentWidget() is window._suite_pages["settings"]
    palette_titles = [command.title for command in window._commands]
    assert "Settings…" in palette_titles and "Data setup…" in palette_titles


def test_leaving_settings_with_edits_by_rail_shortcut_or_escape_asks_and_stay_keeps_the_edits(window, qapp, monkeypatch):
    window._open_settings()
    page = window._suite_pages["settings"]
    asked = []
    monkeypatch.setattr(SettingsPage, "_ask_leave", lambda self: asked.append(True) or LEAVE_STAY)
    page.combo_theme.setCurrentIndex(page.combo_theme.findData("light"))
    window.rail.button("cases").click()
    window._resume_research()
    window._go("handoff")
    window._close_settings()
    assert len(asked) == 4
    assert window.module_stack.currentWidget() is page
    assert window.rail.button("settings").isChecked() and not window.rail.button("cases").isChecked()
    assert page.values()["theme"] == "light"


def test_closing_the_window_with_edits_asks_and_stay_keeps_it_open(window, qapp, monkeypatch):
    window._open_settings()
    page = window._suite_pages["settings"]
    monkeypatch.setattr(SettingsPage, "_ask_leave", lambda self: LEAVE_STAY)
    page.combo_theme.setCurrentIndex(page.combo_theme.findData("light"))
    assert window.close() is False
    assert window.isVisible()
    monkeypatch.setattr(SettingsPage, "_ask_leave", lambda self: LEAVE_DISCARD)
    assert window.close() is True


def test_discarding_on_leave_goes_where_the_person_asked(window, qapp, monkeypatch):
    window._open_settings()
    page = window._suite_pages["settings"]
    monkeypatch.setattr(SettingsPage, "_ask_leave", lambda self: LEAVE_DISCARD)
    page.combo_theme.setCurrentIndex(page.combo_theme.findData("light"))
    window.rail.button("cases").click()
    qapp.processEvents()
    assert window.module_stack.currentWidget() is window.research
    assert window.head.crumb_text() == "Cases"
    assert load_settings()["theme"] == "dark"


def test_saving_in_the_window_applies_the_theme_and_says_what_happened(window, qapp, env):
    from src.desktop.theme import active_theme

    window._open_settings()
    page = window._suite_pages["settings"]
    page.combo_theme.setCurrentIndex(page.combo_theme.findData("light"))
    page.save()
    qapp.processEvents()
    assert active_theme() == "light" and load_settings()["theme"] == "light"
    assert page.status.text() == "Saved: theme. Reloading data from the saved folders."


def test_data_setup_is_one_button_on_the_page_and_a_command_and_no_longer_in_the_local_ai_menu(window, qapp, setup_calls):
    window._open_settings()
    page = window._suite_pages["settings"]
    page.setup_button.click()
    action = next(c.action for c in window._commands if c.title == "Data setup…")
    action.trigger()
    window.research.setup_requested.emit()
    assert setup_calls == [False, False, False]
    menu_texts = [a.text() for a in window.research.model_menu.menu_widget.actions()]
    assert "Data setup" not in menu_texts


def test_the_pages_of_this_stage_hold_no_hard_coded_colours():
    """Every colour of Settings, setup, About and the error card comes from the theme tokens."""
    import re
    from pathlib import Path

    from src.desktop import about_dialog, demo_data, error_card, settings_ui, setup_flow

    colour = re.compile(r"#[0-9A-Fa-f]{6}\b|#[0-9A-Fa-f]{3}\b|QColor\(\s*[\"']|rgba?\(")
    for module in (settings_page, settings_ui, setup_flow, about_dialog, error_card, demo_data):
        found = colour.findall(Path(module.__file__).read_text(encoding="utf-8"))
        assert not found, f"{module.__name__} names a colour itself: {found}"


def test_a_theme_chosen_from_find_while_settings_is_open_is_the_saved_theme_and_survives_a_later_save(
        window, qapp, env):
    window._open_settings()
    page = window._suite_pages["settings"]
    assert page.values()["theme"] == "dark"
    window._set_theme_quick("light")
    assert page.values()["theme"] == "light" and not page.is_dirty()
    page.combo_preset.setCurrentIndex(page.combo_preset.findData("lenient"))
    assert page.changes() == ["rule preset"]
    assert page.save() is True
    assert load_settings()["theme"] == "light" and load_settings()["preset"] == "lenient"


@pytest.mark.parametrize("content,records", [
    (b"PID,Age\nA,1\nB,2\nC,3\n", 3),
    (b"PID,Age\nA,1\nB,2\nC,3", 3),  # no line break after the last record
    (b"PID,Age\r\nA,1\r\nB,2\r\n", 2),
    (b"PID,Procedure\nA,\"Knee, total\"\nB,\"two\nlines\"\nC,\"say \"\"hi\"\"\nthere\"\n", 3),  # a quoted line break
    (b"PID,Age\n", 0),
    (b"", 0),
])
def test_the_surgeries_count_is_the_records_of_the_table(tmp_path, content, records):
    from src.desktop.settings_ui import _count_surgeries

    table = tmp_path / "patient_information.csv"
    table.write_bytes(content)
    assert _count_surgeries(table) == records


def test_the_count_reads_in_bounded_chunks_and_is_left_out_above_the_size_limit(tmp_path, monkeypatch):
    from src.desktop import settings_ui

    table = tmp_path / "patient_information.csv"
    table.write_bytes(b"PID\n" + b"".join(b'"x\ny"\n' for _ in range(5000)))  # quoted breaks across many chunks
    monkeypatch.setattr(settings_ui, "_COUNT_CHUNK_BYTES", 64)
    assert settings_ui._count_surgeries(table) == 5000
    monkeypatch.setattr(settings_ui, "_COUNT_LIMIT_BYTES", table.stat().st_size - 1)
    assert settings_ui._count_surgeries(table) is None
    folder = tmp_path / "emr"
    folder.mkdir()
    for name in ("patient_information.csv", "patient_ventilator.csv", "patient_vitals.csv"):
        (folder / name).write_bytes(table.read_bytes())
    result = settings_ui.check_emr_folder(str(folder))
    assert result.state == "ok" and "surgeries indexed" not in result.text and "3 of 3 required tables found" in result.text


def _check_no_cut_text_and_no_sideways_reach(window, page, qapp):
    """Everything on the current section sits inside the viewport, whole, with no horizontal scroll bar needed."""
    from PySide6.QtWidgets import QAbstractScrollArea, QLabel, QPushButton, QWidget

    scroll = page.stack.currentWidget()
    assert isinstance(scroll, QAbstractScrollArea)
    viewport = scroll.viewport()
    problems = []
    if scroll.horizontalScrollBar().maximum() > 0:
        problems.append(f"sideways scroll of {scroll.horizontalScrollBar().maximum()} px")
    for widget in scroll.widget().findChildren(QWidget):
        if not widget.isVisibleTo(scroll.widget()):
            continue
        right = widget.mapTo(viewport, widget.rect().topRight()).x()
        if right > viewport.width():
            problems.append(f"{type(widget).__name__} {widget.objectName() or widget.accessibleName()!r} ends {right - viewport.width()} px past the edge")
        if isinstance(widget, QLabel) and widget.text():
            if widget.wordWrap():
                if widget.height() < widget.heightForWidth(widget.width()):
                    problems.append(f"wrapped label cut: {widget.text()[:40]!r}")
            elif widget.width() < widget.sizeHint().width():
                problems.append(f"label cut: {widget.text()[:40]!r} ({widget.width()} < {widget.sizeHint().width()})")
        if isinstance(widget, QPushButton) and widget.width() < widget.sizeHint().width():
            problems.append(f"button cut: {widget.text()!r}")
    return problems


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_settings_at_the_windows_least_width_cuts_no_text_and_needs_no_sideways_scrolling(
        window, qapp, theme, tmp_path):
    from src.desktop.settings_page import SECTIONS
    from src.desktop.theme import apply_theme

    apply_theme(qapp, theme)
    window._open_settings()
    page = window._suite_pages["settings"]
    window.resize(100, 700)  # the window refuses to go below its least width
    qapp.processEvents()
    qapp.processEvents()
    least = window.width()
    assert least == max(window.minimumSizeHint().width(), window.minimumWidth()) and page.width() < page.NARROW_BELOW
    # an unsaved change and a long status make the bar's worst case
    page.row_processed.edit.setText(str(tmp_path / "a-processed-folder-with-a-rather-long-name"))
    page.combo_preset.setCurrentIndex(page.combo_preset.findData("lenient"))
    page.row_emr.edit.setText(str(tmp_path / "an-emr-folder-with-a-rather-long-name"))
    page.row_emr.run_check()
    for _ in range(3):  # the bar grows to its wrapped text on the pass after the text changes
        qapp.processEvents()
    assert page._narrow is True
    strip, form = page.sections.geometry(), page.stack.geometry()
    assert strip.bottom() <= form.top(), "the section list sits above the form"
    viewport = page.sections.viewport().rect()
    assert page.sections.width() <= page.width() and all(
        viewport.contains(page.sections.visualItemRect(page.sections.item(row)))
        for row in range(page.sections.count())), "every section name shows whole, none cut or hidden"
    assert page.status.width() >= page.width() - 40, "the status line gets the bar's whole width above the buttons"
    bar_problems = []
    for widget in (page.status, page.discard_button, page.save_button):
        if widget.mapTo(page, widget.rect().bottomRight()).x() > page.width():
            bar_problems.append(widget.objectName() or widget.text())
    assert not bar_problems, bar_problems
    assert page.status.height() >= page.status.heightForWidth(page.status.width())
    for name, _intro in SECTIONS:
        page.show_section(name)
        for _ in range(3):
            qapp.processEvents()
        assert not _check_no_cut_text_and_no_sideways_reach(window, page, qapp), name
    # wide again: the list is back at the left and the bar is one row
    window.resize(1380, 860)
    qapp.processEvents()
    assert page._narrow is False and page.sections.geometry().right() < page.stack.geometry().left()
    assert page.discard_button.geometry().top() == page.status.geometry().top() or \
        abs(page.discard_button.geometry().center().y() - page.status.geometry().center().y()) < 30
    apply_theme(qapp, "dark")
