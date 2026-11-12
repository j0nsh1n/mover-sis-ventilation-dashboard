"""About shows only facts the app knows; the error card says what failed, why and what next, and rewords nothing."""

from __future__ import annotations

import platform
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton, QTextEdit

from src.__version__ import get_version
from src.desktop import about_dialog
from src.desktop.about_dialog import AboutDialog, build_line, licence_text, version_details
from src.desktop.app import MainWindow
from src.desktop.error_card import ErrorCard, failure_detail, show_failure
from src.desktop.errors import error_message, log_path


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def config(monkeypatch, tmp_path):
    monkeypatch.setenv("MOVER_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setattr("src.llm.service.ensure_ollama", lambda **kw: None)


# ----- About -----
def test_about_shows_the_real_version_licence_fonts_and_build(qapp, dispose):
    dialog = dispose(AboutDialog(get_version(), "default @c66420"))
    dialog.show()
    qapp.processEvents()
    from PySide6.QtWidgets import QLabel

    words = " ".join(label.text() for label in dialog.findChildren(QLabel))
    assert f"version {get_version()}" in words
    assert "GNU General Public License v3.0 or later" in words
    assert "License" in words and "Licence" not in words, "one spelling, the one the README and the Licenses button use"
    assert "Inter" in words and "SIL Open Font License 1.1" in words and "Charter" in words and "Bitstream" in words
    assert f"Python {platform.python_version()}" in words and "rules default @c66420" in words
    assert "Research and education only. Not a medical device." in words
    assert dialog.close_button.isDefault() and dialog.focusWidget() is dialog.close_button
    dialog.close()


def test_the_build_line_leaves_out_rules_when_none_is_loaded():
    assert "rules" not in build_line(None) and build_line("strict").endswith("rules strict")


def test_copy_version_details_puts_the_same_facts_on_the_clipboard(qapp, dispose):
    dialog = dispose(AboutDialog("9.9.9", "default"))
    dialog.copy_button.click()
    text = QApplication.clipboard().text()
    assert text == version_details("9.9.9", "default")
    assert text.startswith("MOVER Suite version 9.9.9") and "License: GNU General Public License" in text
    dialog.close()


def test_the_licence_text_holds_the_font_licences_that_ship_with_the_app():
    text = licence_text()
    assert "SIL Open Font License" in text and "Bitstream" in text


def test_a_missing_licence_file_is_said_not_hidden(monkeypatch, tmp_path):
    monkeypatch.setattr(about_dialog, "FONT_DIR", tmp_path)
    assert "could not be read" in licence_text()


def test_about_opens_from_the_window_with_the_loaded_preset(qapp, monkeypatch, dispose):
    monkeypatch.setattr(MainWindow, "_startup", lambda self: None)
    seen = {}
    monkeypatch.setattr(AboutDialog, "exec", lambda self: seen.setdefault("build", build_line(self._rules)) and 0)
    win = dispose(MainWindow())
    win._thresholds_sha256 = "abcdef" + "0" * 58
    win._about()
    win.close()
    assert seen["build"].endswith("rules default @abcdef")


def test_about_opened_from_the_window_names_the_data_on_screen(qapp, monkeypatch, dispose):
    from src.desktop.about_dialog import DATA_FACT, DEMO_DATA_FACT, facts

    assert dict(facts(True))["Data"] == DEMO_DATA_FACT and dict(facts(False))["Data"] == DATA_FACT
    assert "fictional demo data" in DEMO_DATA_FACT.lower() and "MOVER" not in DEMO_DATA_FACT
    monkeypatch.setattr(MainWindow, "_startup", lambda self: None)
    seen = []
    real_init = AboutDialog.__init__

    def spy(self, *args, **kwargs):
        seen.append(kwargs.get("demo"))
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(AboutDialog, "__init__", spy)
    monkeypatch.setattr(AboutDialog, "exec", lambda self: 0)
    win = dispose(MainWindow())
    win._shown_data_is_demo = False
    win._about()
    win._shown_data_is_demo = True
    win._about()
    win.close()
    assert seen == [False, True]


# ----- the error card -----
def test_the_card_says_what_why_and_next_and_keeps_the_detail_collapsed(qapp, dispose):
    card = dispose(ErrorCard(None, what="The pipeline did not finish.", why="The EMR folder is missing one table.",
                     next_step="Choose another folder.", detail="Code: pipeline:DataValidationError"))
    assert card.text() == "The pipeline did not finish."
    assert card.informativeText() == "The EMR folder is missing one table.\n\nChoose another folder."
    assert card.words().splitlines()[0] == "The pipeline did not finish."
    assert card.detailedText() == "Code: pipeline:DataValidationError"
    assert card.has_detail() and card.objectName() == "errorCard"
    assert card.accessibleName() == "The pipeline did not finish."
    card.show()
    qapp.processEvents()
    # the details text is hidden until asked for, and is selectable so it can be copied
    area = card.findChild(QTextEdit)
    assert area is not None and not area.isVisible()
    toggle = next(b for b in card.findChildren(QPushButton) if "etails" in b.text())
    toggle.click()
    qapp.processEvents()
    assert area.isVisible() and area.isReadOnly() and area.textInteractionFlags() & area.textInteractionFlags().TextSelectableByMouse
    card.close()


def test_the_card_shows_the_message_it_was_given_word_for_word(dispose):
    message = error_message("load", ValueError("PRIVATE /a/b.csv"))
    card = dispose(ErrorCard(None, what="The analyzed cases could not be loaded.", why=message))
    assert card.why == message and message in card.words()
    assert "PRIVATE" not in card.words() and "PRIVATE" not in card.detailedText()
    # the log is shown in full, except that a home folder is written as "~" so the account name stays out
    try:
        shown = "~/" + log_path().relative_to(Path.home()).as_posix()
    except ValueError:
        shown = str(log_path())
    assert card.detailedText() == f"Code: load:ValueError\nLog: {shown}"


def test_the_log_place_is_written_with_the_home_folder_as_a_tilde_and_never_with_the_account_name(
        qapp, monkeypatch, tmp_path, dispose):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))  # where Windows looks for the home folder
    monkeypatch.setenv("MOVER_CONFIG_DIR", str(home / ".config" / "mover-suite"))
    message = error_message("load", ValueError("x"))
    assert "~" not in message and str(home) not in message, "the message itself never carried the path"
    card = dispose(ErrorCard(None, what="The analyzed cases could not be loaded.", why=message))
    assert card.detailedText() == "Code: load:ValueError\nLog: ~/.config/mover-suite/app-errors.log"
    assert str(home) not in card.detailedText() and str(home) not in card.words()
    # a log outside the home folder has no account name to hide
    monkeypatch.setenv("MOVER_CONFIG_DIR", str(tmp_path / "elsewhere"))
    assert failure_detail(message).endswith(f"Log: {tmp_path / 'elsewhere' / 'app-errors.log'}")


def test_a_failure_and_a_warning_differ_in_shape_and_word_not_colour_alone(qapp, dispose):
    failure = dispose(ErrorCard(None, what="The pipeline did not finish.", level="critical"))
    warning = dispose(ErrorCard(None, what="The update could not start.", level="warning"))
    assert failure.mark == "◆ critical" and warning.mark == "△ warn", "the rule flags' own shape and word"
    assert failure.accessibleDescription().startswith("◆ critical. ")
    assert warning.accessibleDescription().startswith("△ warn. ")
    images = {}
    for name, card in (("failure", failure), ("warning", warning)):
        pixmap = card.iconPixmap()
        images[name] = pixmap.toImage()
    assert images["failure"] != images["warning"]
    ratio = failure.iconPixmap().devicePixelRatio()
    unit = 40 / 24.0 * ratio
    left = (40 * 1.6 - 40) / 2 * ratio
    # a point inside the diamond that lies in the hollow of the triangle: solid for a failure, empty for a warning
    probe = (int(left + 9 * unit), int(15 * unit))
    assert images["failure"].pixelColor(*probe).alpha() > 200
    assert images["warning"].pixelColor(*probe).alpha() == 0
    # the word is drawn under the shape: some ink below the shape's own rows in both
    for image in images.values():
        below = [image.pixelColor(x, y).alpha() for x in range(image.width()) for y in range(int(40 * ratio) + 2, image.height())]
        assert max(below) > 100


def test_a_message_with_no_code_or_log_has_no_detail_area():
    assert failure_detail("The folder cannot be used.") == ""
    assert not ErrorCard(None, what="x", why="The folder cannot be used.").has_detail()


def test_show_failure_waits_like_the_dialog_it_replaces(qapp, error_cards):
    assert show_failure(None, what="a", why="b", next_step="c", title="T") == 0
    (card,) = error_cards
    assert isinstance(card, QMessageBox) and card.windowTitle() == "T" and card.level == "critical"


def test_every_failure_box_the_window_raises_is_an_error_card(qapp, monkeypatch, error_cards, tmp_path, dispose):
    monkeypatch.setattr(MainWindow, "_startup", lambda self: None)
    win = dispose(MainWindow())
    win._on_pipeline_fail("The operation could not finish (pipeline:X). Details are in app-errors.log in the settings folder.")
    win._on_corpus_scan_fail("The full-EMR scan could not finish (scan:ValueError). Details are in X.")
    win._update_failed_to_start("disk full")
    win._apply_data_folder(tmp_path / "no-such-folder")
    titles = [card.windowTitle() for card in error_cards]
    assert titles == ["Pipeline / guardrail error", "Full-EMR scan", "Update could not start", "Invalid data folder"]
    assert [card.level for card in error_cards] == ["critical", "critical", "warning", "critical"]
    for card in error_cards:
        assert card.text() and card.why and isinstance(card, ErrorCard)
    assert "pipeline:X" in error_cards[0].detailedText()
    win.close()
