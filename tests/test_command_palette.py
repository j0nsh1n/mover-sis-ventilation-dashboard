"""Ctrl+K lists every action, screen and setting, and every shortcut still works with no menu bar."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QAccessible, QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QStyleOptionViewItem

from src.desktop.app import MainWindow
from src.desktop.command_palette import Command, match_rank

# What the owner listed as the old File, View and Help menus, the module keys and Settings.
# Written from that list, not read back from the code: (what to type in Find, its shortcut, what it does).
CALLS = [
    ("Open EMR folder", "Ctrl+O", "_browse_emr_folder"),
    ("Open Wave folder", "Ctrl+Shift+O", "_browse_wave_folder"),
    ("Import articles", None, "_import_articles"),
    ("Reload processed data", "Ctrl+R", "_autoload_processed"),
    ("Run pipeline (50 cases)", None, "_run_pipeline_menu"),
    ("Scan full EMR", None, "_run_corpus_scan"),
    ("Data setup", None, "_open_setup_wizard"),
    ("Exit", "Ctrl+Q", "_quit_app"),
    ("Theme: Light", None, ("_set_theme_quick", "light")),
    ("Theme: Dark", None, ("_set_theme_quick", "dark")),
    ("Theme: System", None, ("_set_theme_quick", "system")),
    ("About", None, "_about"),
    ("Restart to finish update", None, "_restart_for_update"),
    ("Settings", "Ctrl+,", "_open_settings"),
]
# Page switches have a visible effect rather than a handler call: (what to type, shortcut, page object name).
PAGES = [
    ("Monitoring", "Ctrl+1", None),
    ("Handoff practice", "Ctrl+2", "handoff-practice"),
    ("Medication and vitals", "Ctrl+3", "medication-vitals"),
    ("WHO checklist", "Ctrl+4", "who-checklist"),
]


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def calls(monkeypatch):
    log: list = []

    def recorder(name):
        return lambda self, *args, **kwargs: log.append(name)

    for _text, _shortcut, handler in CALLS:
        if isinstance(handler, str):
            monkeypatch.setattr(MainWindow, handler, recorder(handler))
    monkeypatch.setattr(MainWindow, "_set_theme_quick", lambda self, mode: log.append(("_set_theme_quick", mode)))
    return log


@pytest.fixture
def window(qapp, monkeypatch, tmp_path, calls):
    monkeypatch.setenv("MOVER_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("MOVER_EMR_DIR", str(tmp_path / "EMR"))
    monkeypatch.setenv("MOVER_PROCESSED_DIR", str(tmp_path / "processed"))
    monkeypatch.setattr(MainWindow, "_startup", lambda self: None)
    win = MainWindow()
    win.show()
    qapp.processEvents()
    yield win
    # The Exit command is recorded, not run, so close the window the ordinary way
    win.close()
    win.deleteLater()
    qapp.processEvents()


def _open_palette(window, qapp):
    QTest.keyClick(window, Qt.Key.Key_K, Qt.KeyboardModifier.ControlModifier)
    qapp.processEvents()
    assert window._palette is not None and window._palette.isVisible()
    return window._palette


def _rows(palette):
    return [(palette.results.item(row).text(), palette.results.item(row).data(Qt.ItemDataRole.UserRole + 1))
            for row in range(palette.results.count())]


def _press(widget, key, modifiers=Qt.KeyboardModifier.NoModifier):
    QTest.keyClick(widget, key, modifiers)


def test_every_old_menu_action_is_found_in_ctrl_k_and_runs_from_there(window, qapp, calls):
    window.update_act.setVisible(True)  # the update item is offered once an update is ready
    for text, shortcut, handler in CALLS:
        calls.clear()
        palette = _open_palette(window, qapp)
        QTest.keyClicks(palette.search, text)
        rows = _rows(palette)
        assert rows, f"{text!r} is not reachable from Ctrl+K"
        title, shown = rows[0]
        assert title.lower().startswith(text.lower().split(" (")[0]), (text, rows[:3])
        assert (shown or None) == shortcut, (text, shown)
        _press(palette.search, Qt.Key.Key_Return)
        qapp.processEvents()
        assert calls == [handler], text
        assert window._palette is None, "Enter closes the palette"


def test_every_old_shortcut_still_triggers_its_action_with_no_menu_bar(window, qapp, calls):
    assert window.menuWidget() is None
    # Practice pages hold none of the research keys, so Ctrl+R reaches the window's Reload action
    window._go("handoff")
    qapp.processEvents()
    for text, shortcut, handler in CALLS:
        if shortcut is None or shortcut == "Ctrl+1":
            continue
        calls.clear()
        QTest.keySequence(window, QKeySequence(shortcut))
        qapp.processEvents()
        assert calls == [handler], f"{shortcut} ({text})"


def test_ctrl_r_still_opens_the_rules_on_research_pages_and_reloads_elsewhere(window, qapp, calls):
    window.research.question_edit.setFocus()
    qapp.processEvents()
    QTest.keyClick(window.research.question_edit, Qt.Key.Key_R, Qt.KeyboardModifier.ControlModifier)
    qapp.processEvents()
    assert window.research.rules_drawer.is_open
    assert calls == []
    window.research.rules_drawer.close()
    window._go("who")
    qapp.processEvents()
    QTest.keyClick(window, Qt.Key.Key_R, Qt.KeyboardModifier.ControlModifier)
    assert calls == ["_autoload_processed"]


@pytest.mark.parametrize(("text", "shortcut", "page"), PAGES)
def test_module_shortcuts_and_ctrl_k_switch_pages(window, qapp, text, shortcut, page):
    def shown() -> str:
        return window.module_stack.currentWidget().objectName()

    for route in ("shortcut", "palette"):
        if page is None:
            # Research comes back as it was left: here the cases page, not the question board
            window._go("cases")
            window._go("handoff")
        else:
            window._go("research")  # start somewhere else
        qapp.processEvents()
        if route == "shortcut":
            QTest.keySequence(window, QKeySequence(shortcut))
        else:
            palette = _open_palette(window, qapp)
            QTest.keyClicks(palette.search, text)
            assert _rows(palette)[0][1] == shortcut
            _press(palette.search, Qt.Key.Key_Return)
        qapp.processEvents()
        if page is None:
            assert window.module_stack.currentWidget() is window.research
            assert window.research.stack.currentIndex() == 2
        else:
            assert shown() == page


def test_restart_item_is_listed_only_once_an_update_is_ready(window, qapp):
    palette = _open_palette(window, qapp)
    QTest.keyClicks(palette.search, "restart")
    assert _rows(palette) == []
    _press(palette.search, Qt.Key.Key_Escape)
    qapp.processEvents()
    window._on_update_ready("9.9.9", None)
    palette = _open_palette(window, qapp)
    QTest.keyClicks(palette.search, "restart")
    assert [title for title, _ in _rows(palette)] == ["Restart to finish update"]


def test_ctrl_k_opens_from_a_text_field_and_from_the_find_button(window, qapp):
    window.research.question_edit.setFocus()
    qapp.processEvents()
    window.research.question_edit.setText("keep this")
    QTest.keyClick(window.research.question_edit, Qt.Key.Key_K, Qt.KeyboardModifier.ControlModifier)
    qapp.processEvents()
    assert window._palette is not None and window._palette.isVisible()
    assert window.research.question_edit.text() == "keep this"
    _press(window._palette.search, Qt.Key.Key_Escape)
    qapp.processEvents()
    assert window._palette is None

    window.head.find_button.click()
    qapp.processEvents()
    assert window._palette is not None and window._palette.search.hasFocus()


def test_escape_closes_without_running_and_arrows_move_before_enter_runs(window, qapp, calls):
    palette = _open_palette(window, qapp)
    assert palette.search.hasFocus()
    _press(palette.search, Qt.Key.Key_Escape)
    qapp.processEvents()
    assert window._palette is None and calls == []

    palette = _open_palette(window, qapp)
    QTest.keyClicks(palette.search, "theme:")
    assert [title for title, _ in _rows(palette)] == ["Theme: Light", "Theme: Dark", "Theme: System"]
    assert palette.results.currentRow() == 0
    _press(palette.search, Qt.Key.Key_Down)
    _press(palette.search, Qt.Key.Key_Down)
    assert palette.results.currentRow() == 2
    _press(palette.search, Qt.Key.Key_Down)
    assert palette.results.currentRow() == 2, "the selection stops at the last result"
    _press(palette.search, Qt.Key.Key_Up)
    _press(palette.search, Qt.Key.Key_Return)
    qapp.processEvents()
    assert calls == [("_set_theme_quick", "dark")]


def test_an_unavailable_command_says_why_and_enter_does_not_run_it(window, qapp):
    palette = _open_palette(window, qapp)
    QTest.keyClicks(palette.search, "signal")
    title, _shortcut = _rows(palette)[0]
    assert title == "Signal"
    item = palette.results.item(0)
    assert not item.flags() & Qt.ItemFlag.ItemIsEnabled
    assert "open case" in item.data(Qt.ItemDataRole.UserRole + 4).lower()
    assert "open case" in item.data(Qt.ItemDataRole.AccessibleTextRole).lower()
    # a mouse click or a stale selection can still land on it; Enter must not close the palette as if it ran
    palette.results.setCurrentItem(item)
    assert palette.results.currentItem() is item
    _press(palette.search, Qt.Key.Key_Return)
    qapp.processEvents()
    assert window._palette is palette and palette.isVisible()
    assert window.research.stack.currentIndex() == 0


def test_no_match_says_so_and_enter_runs_nothing(window, qapp, calls):
    palette = _open_palette(window, qapp)
    QTest.keyClicks(palette.search, "zzzzqq")
    assert palette.count.text() == "No results"
    _press(palette.search, Qt.Key.Key_Return)
    qapp.processEvents()
    assert calls == [] and window._palette is palette


def test_the_palette_is_usable_by_a_screen_reader(window, qapp):
    palette = _open_palette(window, qapp)
    assert palette.search.accessibleName() == "Search actions, screens and settings"
    assert palette.results.accessibleName() == "Results"
    assert palette.windowTitle() == "Find"
    QTest.keyClicks(palette.search, "open emr")
    row = palette.results.item(0).data(Qt.ItemDataRole.AccessibleTextRole)
    assert row == "Open EMR folder…, shortcut Ctrl+O, Data"
    assert QAccessible.queryAccessibleInterface(palette.results) is not None
    assert palette.count.text() == "1 result"
    assert palette.search.focusPolicy() != Qt.FocusPolicy.NoFocus
    assert palette.results.focusPolicy() != Qt.FocusPolicy.NoFocus


# The research page's own commands (and the installed models, when there are any) sit after the places.
PAGE_GROUPS = ["Go to", "Research", "Question scope", "Local AI", "Data", "Appearance", "App"]


def test_the_list_has_every_command_once_in_group_order_with_no_filter(window, qapp):
    palette = _open_palette(window, qapp)
    titles = [title for title, _ in _rows(palette)]
    assert len(titles) == len(set(titles))
    assert titles[:3] == ["Research", "Cases", "Signal"]
    groups = [palette.results.item(row).data(Qt.ItemDataRole.UserRole + 2) for row in range(palette.results.count())]
    assert groups == sorted(groups, key=PAGE_GROUPS.index)
    # The rules key is handled by the research pages, so the palette says where it applies
    assert dict(_rows(palette))["Rules"] == "Ctrl+R on research pages"


def test_the_active_theme_is_marked_and_follows_a_change(window, qapp):
    from src.user_settings import update_settings

    update_settings(theme="dark")
    window._sync_theme_actions()
    palette = _open_palette(window, qapp)
    QTest.keyClicks(palette.search, "theme:")
    marks = {palette.results.item(row).text(): palette.results.item(row).data(Qt.ItemDataRole.UserRole + 4)
             for row in range(palette.results.count())}
    assert marks == {"Theme: Light": None, "Theme: Dark": "active", "Theme: System": None}


def test_matching_needs_every_word_and_prefers_titles_that_start_with_it():
    from PySide6.QtGui import QAction

    reload_ = Command(QAction("Reload processed data"), "Data", "file refresh cache")
    rules = Command(QAction("Rules"), "Go to", "reference thresholds search rule")
    assert match_rank(reload_, "reload data") == 0
    assert match_rank(reload_, "data reload") == 1
    assert match_rank(reload_, "cache") == 2
    assert match_rank(reload_, "reload rules") is None
    assert match_rank(rules, "") == 3


def _unit_palette():
    from PySide6.QtGui import QAction

    from src.desktop.command_palette import CommandPalette

    commands = [Command(QAction(text), group) for text, group in (
        ("Open EMR folder", "Data"), ("Open Wave folder", "Data"), ("Reload", "Data"), ("Cases", "Go to"),
    )]
    return CommandPalette(commands)


def test_each_keystroke_announces_once_with_the_real_top_row(qapp, monkeypatch):
    import src.desktop.command_palette as module

    said: list[str] = []
    monkeypatch.setattr(module, "announce", lambda _widget, message: said.append(message))
    palette = _unit_palette()
    said.clear()
    QTest.keyClicks(palette.search, "o")
    assert said == ["Open EMR folder, Data, 1 of 4"]
    said.clear()
    QTest.keyClicks(palette.search, "p")
    assert said == ["Open EMR folder, Data, 1 of 2"]
    said.clear()
    QTest.keyClicks(palette.search, "zzzz")
    assert said == ["No results"] * 4, "an empty list says so once per keystroke, never a stale row"
    palette.search.clear()
    assert said[-1].endswith("1 of 4") and "No results" not in said[-1]


def test_no_results_is_announced_only_when_the_list_is_empty(qapp, monkeypatch):
    import src.desktop.command_palette as module

    said: list[str] = []
    monkeypatch.setattr(module, "announce", lambda _widget, message: said.append(message))
    palette = _unit_palette()
    said.clear()
    palette.search.setText("reload")
    assert said == ["Reload, Data, 1 of 1"]
    assert "No results" not in said


def test_each_group_is_one_section_with_a_heading_that_cannot_be_selected(window, qapp):
    from src.desktop.command_palette import HEADING_ROLE

    palette = _open_palette(window, qapp)
    results = palette.results
    headings = [(row, results.item(row).data(HEADING_ROLE)) for row in range(results.count())
                if results.item(row).data(HEADING_ROLE)]
    assert [name for _row, name in headings] == PAGE_GROUPS
    # headings are painted above their first row, so every list item is a runnable command
    assert all(results.item(row).data(Qt.ItemDataRole.UserRole + 3) is not None for row in range(results.count()))
    # the group name is not repeated on every row
    delegate_height = results.itemDelegate().sizeHint
    option = QStyleOptionViewItem()
    first, second = results.model().index(0, 0), results.model().index(1, 0)
    assert delegate_height(option, first).height() > delegate_height(option, second).height()
    # Down from the last "Go to" row lands on the first row of the next group
    next_row = headings[1][0]
    results.setCurrentRow(next_row - 1)
    _press(palette.search, Qt.Key.Key_Down)
    assert results.currentRow() == next_row

    palette.search.setText("theme")
    rows = [results.item(row).data(Qt.ItemDataRole.UserRole + 2) for row in range(results.count())]
    assert rows == sorted(rows, key=rows.index), "rows of one group stay together"
    assert sum(1 for row in range(results.count()) if results.item(row).data(HEADING_ROLE)) == len(set(rows))


def test_the_palette_is_never_wider_than_the_window(window, qapp):
    window.resize(400, 600)
    qapp.processEvents()
    palette = _open_palette(window, qapp)
    assert palette.width() <= window.width() - 2 * 24
    _press(palette.search, Qt.Key.Key_Escape)
    qapp.processEvents()
    window.resize(1380, 860)
    qapp.processEvents()
    palette = _open_palette(window, qapp)
    assert palette.width() == 640


def test_headings_are_written_as_the_group_name_without_capitals(window, qapp):
    from src.desktop.command_palette import HEADING_ROLE, heading_text

    palette = _open_palette(window, qapp)
    results = palette.results
    for row in range(results.count()):
        index = results.model().index(row, 0)
        group = results.item(row).data(HEADING_ROLE)
        if group:
            assert heading_text(index) == group and heading_text(index) != group.upper(), group


def test_a_click_on_a_heading_runs_nothing_and_a_click_on_the_row_runs_it(window, qapp, calls):
    from src.desktop.command_palette import HEADING_HEIGHT, HEADING_ROLE

    palette = _open_palette(window, qapp)
    results = palette.results
    row = next(r for r in range(results.count()) if results.item(r).data(HEADING_ROLE) == "Data")
    item = results.item(row)
    handler = next(h for text, _s, h in CALLS if item.text().startswith(text))
    rect = results.visualItemRect(item)
    QTest.mouseClick(results.viewport(), Qt.MouseButton.LeftButton, pos=rect.topLeft() + QPoint(40, HEADING_HEIGHT // 2))
    qapp.processEvents()
    assert calls == [] and palette.isVisible()
    QTest.mouseClick(results.viewport(), Qt.MouseButton.LeftButton,
                     pos=rect.topLeft() + QPoint(40, HEADING_HEIGHT + (rect.height() - HEADING_HEIGHT) // 2))
    qapp.processEvents()
    assert calls == [handler]


def test_only_a_left_click_on_one_row_runs_it(window, qapp, calls):
    palette = _open_palette(window, qapp)
    palette.search.setText("Reload")
    qapp.processEvents()
    results = palette.results
    item = results.item(0)
    centre = results.visualItemRect(item).center()
    for button in (Qt.MouseButton.RightButton, Qt.MouseButton.MiddleButton):
        QTest.mouseClick(results.viewport(), button, pos=centre)
        qapp.processEvents()
        assert calls == [] and palette.isVisible(), button
    elsewhere = results.visualItemRect(results.item(results.count() - 1)).center() if results.count() > 1 else centre + QPoint(0, 400)
    QTest.mousePress(results.viewport(), Qt.MouseButton.LeftButton, pos=elsewhere)
    QTest.mouseRelease(results.viewport(), Qt.MouseButton.LeftButton, pos=centre)
    qapp.processEvents()
    assert calls == [] and palette.isVisible(), "a press on another spot then release on the row runs nothing"
    QTest.mouseClick(results.viewport(), Qt.MouseButton.LeftButton, pos=centre)
    qapp.processEvents()
    assert calls == ["_autoload_processed"]
