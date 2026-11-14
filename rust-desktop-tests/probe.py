"""Observe production Python/Qt behavior for the Rust contract runner."""

from __future__ import annotations

import contextlib
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


def emit(key, value):
    if isinstance(value, bool):
        encoded = f"b:{int(value)}"
    elif isinstance(value, int):
        encoded = f"u:{value}"
    else:
        encoded = "t:" + str(value).encode("utf-8").hex()
    print(f"{key}={encoded}")


def pump(app):
    deadline = time.monotonic() + .15
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.005)


def qt():
    from PySide6.QtWidgets import QApplication
    from src.desktop.theme import apply_theme

    app = QApplication.instance() or QApplication([])
    apply_theme(app, "light")
    return app


def window():
    from src.desktop.app import MainWindow

    with patch.object(MainWindow, "_startup", lambda self: None):
        return MainWindow()


def no_ollama():
    return patch("src.llm.service.ensure_ollama", return_value=SimpleNamespace(models=[], available=False))


def prepare(root):
    from src.pipeline.synthetic import generate_synthetic_emr

    source = root / "source"
    generate_synthetic_emr(source, n_cases=2, seed=4)
    return {"source_files": len(list(source.rglob("*")))}


def output_route(root, route, variant):
    source = root / "source"
    os.environ["MOVER_ALLOW_EXTERNAL_OUTPUT"] = "1"
    os.environ["MOVER_EMR_DIR"] = str(source)
    output = source if variant == "equal" else source / "processed"
    if variant == "symlink":
        alias = root / "alias"
        alias.symlink_to(source, target_is_directory=True)
        output = alias / "processed"
    elif variant == "sibling":
        output = root / "sibling"
    if route == "settings":
        from src.desktop.settings_page import SettingsPage
        from src.desktop.settings_ui import apply_values

        app = qt()
        with no_ollama():
            form = SettingsPage()
        form.row_emr.edit.setText(str(source))
        form.row_processed.edit.setText(str(output))
        form.row_wave.edit.clear()
        form.row_models.edit.clear()
        def action():
            return apply_values(form.values(), require_emr=True)
    elif route == "pipeline":
        from src.pipeline.run import run_pipeline

        def action():
            return run_pipeline(emr_dir=source, output_dir=output, n_cases=1)
    else:
        from src.pipeline.corpus_scan import scan_corpus

        def action():
            return scan_corpus(emr_dir=source, output_dir=output, workers=1)
    error = ""
    try:
        with contextlib.redirect_stdout(sys.stderr):
            action()
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    if route == "settings":
        form.close()
        app.processEvents()
    return {"error": error, "output": str(output.resolve())}


def frames(pid):
    import pandas as pd

    return (
        pd.DataFrame({"PID": [pid], "anomaly_score": [0], "Procedure": ["Synthetic procedure"]}),
        pd.DataFrame({"PID": [pid], "Obs_time": [pd.Timestamp("2016-01-01")], "t_min": [0.], "PIP": [20.], "HR": [70.], "ETCO2": [38.]}),
        pd.DataFrame(columns=["PID", "rule_id", "severity", "t_min"]),
        pd.DataFrame(), pd.DataFrame(),
    )


def cache(root, source, pid):
    root.mkdir(parents=True, exist_ok=True)
    for name, frame in zip(["cases", "timeseries", "flags", "episodes", "events"], frames(pid), strict=True):
        frame.to_parquet(root / f"{name}.parquet", index=False)
    (root / "run_meta.json").write_text(json.dumps({"emr_dir": str(source), "preset": "default"}))


def provenance(root, variant):
    from src.desktop.app import DataLoadWorker

    app = qt()
    processed = root / "processed"
    cache(processed, root / ("source" if variant == "matching" else "other-source"), "SYN_A")
    worker = DataLoadWorker(processed=processed)
    loaded, errors = [], []
    worker.finished_ok.connect(loaded.append)
    worker.failed.connect(errors.append)
    worker.run()
    app.processEvents()
    return {"error": "\n".join(errors) + "\n" + (root / "config/app-errors.log").read_text() if errors else "",
            "case_ids": ",".join(loaded[0][0]["PID"]) if loaded else ""}


def switch_source(root):
    import src.desktop.app as desktop
    from PySide6.QtWidgets import QMessageBox

    app = qt()
    dialogs = []

    class ScheduledLoad(desktop.DataLoadWorker):
        def start(self):
            pass

        def isRunning(self):
            return False

    with patch.object(desktop, "DataLoadWorker", ScheduledLoad), patch.object(QMessageBox, "open", lambda box: dialogs.append(getattr(box, "words", box.text)())):
        win = window()
        a, b = root / "source-a", root / "source-b"
        proc_a, proc_b = root / "processed-a", root / "processed-b"
        cache(proc_a, a, "SYN_A")
        cache(proc_b, b, "SYN_B")
        os.environ.update(MOVER_EMR_DIR=str(a), MOVER_PROCESSED_DIR=str(proc_a))
        win._autoload_processed()
        old = win._worker
        old.finished_ok.emit(frames("SYN_A"))
        os.environ.update(MOVER_EMR_DIR=str(b), MOVER_PROCESSED_DIR=str(proc_b))
        win._autoload_processed()
        cleared = sum(frame is not None for frame in [win.cases, win.ts, win.flags, win.episodes, win.events])
        facts = len(win._analysis_facts)
        current = win._worker
        current.finished_ok.emit(frames("SYN_B"))
        old.finished_ok.emit(frames("SYN_A"))
        ids = ",".join(win.cases["PID"]) if win.cases is not None else ""
        old.failed.emit("load:RuntimeError")
        after_failure = ",".join(win.cases["PID"]) if win.cases is not None else ""
        win.close()
        pump(app)
    return {"frames_after_switch": cleared, "facts_after_switch": facts, "case_ids": ids,
            "case_ids_after_late_failure": after_failure, "stale_dialogs": len(dialogs)}


def errors(root, variant):
    from src.desktop.app import DataLoadWorker, PipelineWorker
    from src.desktop.research_workers import CaseFetchWorker, ResearchWorker, MinuteQuestionWorker

    app = qt()
    marker = "FICTIONAL_PRIVATE_MARKER private-record.csv\nTraceback fictional detail"

    def fail(*args, **kwargs):
        raise RuntimeError(marker)

    if variant == "log-failure":
        from src.desktop.errors import error_message

        blocked = root / "blocked-config"
        blocked.write_text("synthetic file")
        os.environ["MOVER_CONFIG_DIR"] = str(blocked)
        return {"message": error_message("load", RuntimeError(marker))}
    messages = []
    if variant == "existing-log":
        (root / "config").mkdir(exist_ok=True)
        (root / "config/app-errors.log").write_text("earlier entry\n")
        (root / "config/app-errors.log").chmod(0o644)
        variant = "load"
    if variant == "load":
        worker, target = DataLoadWorker(processed=root), "src.services.data.load_processed"
    elif variant == "pipeline":
        worker, target = PipelineWorker(1, "default", True), "src.services.data.ensure_data"
    elif variant == "case":
        worker, target = CaseFetchWorker("SYN_A", root), "src.desktop.research_workers.fetch_cases"
    elif variant == "research":
        worker, target = ResearchWorker(SimpleNamespace(search=fail), "question", None, None), None
    elif variant == "minute-question":
        worker = MinuteQuestionWorker(None, "synthetic extract", "question", "unused-model")
        target = "src.desktop.research_workers.answer_from_minute"
    else:
        from PySide6.QtWidgets import QMessageBox

        processed = root / "processed"
        processed.mkdir()
        (processed / "corpus_cases.parquet").write_text(marker)
        (processed / "corpus_meta.json").write_text("{}")
        with patch.object(QMessageBox, "open", lambda box: messages.append(getattr(box, "words", box.text)())):
            win = window()
            with patch("src.pipeline.corpus_scan.load_corpus_scan", side_effect=RuntimeError(marker)):
                win._load_corpus_scan()
            win.close()
        worker = None
    if worker is not None:
        worker.failed.connect(messages.append)
        with patch(target, fail) if target else contextlib.nullcontext():
            worker.run()
    app.processEvents()
    log = root / "config/app-errors.log"
    return {"message": "\n".join(messages), "log": log.read_text() if log.exists() else "",
            "log_mode": log.stat().st_mode & 0o777 if log.exists() else 0}


def announcements(root):
    from PySide6.QtGui import QAccessible, QAccessibleAnnouncementEvent

    app = qt()
    events = []

    def record(event):
        # Only an announcement has a message; building the window also sends property-change events,
        # such as the AI button's new description, which are cleared below and checked by type after.
        message = event.message() if isinstance(event, QAccessibleAnnouncementEvent) else None
        events.append((event.type().name, message))

    with patch.object(QAccessible, "updateAccessibility", record):
        win = window()
        events.clear()
        win.research.set_status("Synthetic status complete")
        win.research.set_status("Synthetic status complete")
        win.statusBar().showMessage("Synthetic pipeline complete")
        win.research.begin_question("Synthetic question", request_id=17)
        win.research.set_answer("Synthetic answer", request_id=17)
        win.close()
        pump(app)
    return {"repeated_status_count": sum(message == "Synthetic status complete" for _, message in events),
            "status_bar_count": sum(message == "Synthetic pipeline complete" for _, message in events),
            "answer_count": sum(message == "Research answer ready for Synthetic question." for _, message in events),
            "event_types": "\n".join(kind for kind, _ in events)}


def accessible_name(widget):
    from PySide6.QtGui import QAccessible

    interface = QAccessible.queryAccessibleInterface(widget)
    return interface.text(QAccessible.Text.Name) if interface else ""


def names(root):
    from PySide6.QtWidgets import QPushButton, QTableWidget
    from src.desktop.settings_page import SettingsPage
    from src.desktop.shell import RailButton

    app = qt()
    with no_ollama():
        form = SettingsPage()
    win = window()
    result = {key: accessible_name(widget) for key, widget in [
        ("emr", form.row_emr.edit), ("wave", form.row_wave.edit), ("processed", form.row_processed.edit),
        ("models", form.row_models.edit), ("question", win.research.question_edit)]}
    actions = {button.text(): button for button in win.findChildren(QPushButton)}
    actions.update({button.text(): button for button in win.rail.findChildren(RailButton)})
    actions.update({"AI": win.research.model_menu, "Trace the evidence": win.research.ask_button})
    for label in ["Research", "Cases", "Signal", "Practice", "Rules", "Settings", "Clear board", "New thread",
                  "AI", "Trace the evidence"]:
        result[label.replace(" ", "_").lower()] = accessible_name(actions[label]) if label in actions else ""
    result["tables"] = "\n".join(accessible_name(table) for table in win.findChildren(QTableWidget))
    form.close()
    win.close()
    app.processEvents()
    return result


def border_image(widget):
    from PySide6.QtCore import Qt

    image = widget.grab().toImage()
    return [image.pixelColor(x, y).rgba() for y in range(image.height()) for x in range(image.width())
            if x < 3 or y < 3 or x >= image.width() - 3 or y >= image.height() - 3], Qt


def focus(root):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QMessageBox

    from src.services.retrieval import RetrievalResult, SearchCandidate

    app = qt()
    win = window()
    win.research.set_setup_required(False)
    # The shortlist table stays hidden until a search returns a case, and a hidden table has no focus to show.
    candidate = SearchCandidate("SYN_A", "analyzed case", True, "Synthetic procedure", 55, "F", "PID SYN_A.", "keyword", 0.5)
    win.research.set_results(RetrievalResult(candidates=(candidate,)))
    win.show()
    pump(app)
    stops = []
    for _ in range(24):
        focused = app.focusWidget()
        if focused is not None and focused.isVisible():
            stops.append(accessible_name(focused))
        QTest.keyClick(win, Qt.Key.Key_Tab)
        app.processEvents()
    result = {"tab_stops": len(stops), "unnamed_stops": sum(not text.strip() for text in stops)}
    for key, widget in [("model_menu", win.research.model_menu), ("trace", win.research.ask_button),
                        ("rail", win.rail.button("cases")), ("table", win.research.candidate_table)]:
        win.research.question_edit.setFocus()
        pump(app)
        before, _ = border_image(widget)
        widget.setFocus()
        pump(app)
        after, _ = border_image(widget)
        result[f"{key}_border_changes"] = sum(a != b for a, b in zip(before, after, strict=True))
    win._warn("Synthetic warning", "Synthetic safe error")
    pump(app)
    for box in win.findChildren(QMessageBox):
        box.accept()
    pump(app)
    focused = win.focusWidget()
    result["warning_focus_name"] = accessible_name(focused) if focused else ""
    print("offscreen active window after warning:", repr(app.activeWindow()), file=sys.stderr)
    win.grab().save(str(root / "focus.png"))
    win.close()
    return result


def painted(view, item, role):
    """True when the row's pixels change once the delegate's data for ``role`` is taken away."""
    rect = view.visualItemRect(item)
    before = view.viewport().grab(rect).toImage()
    kept = item.data(role)
    item.setData(role, "")
    after = view.viewport().grab(rect).toImage()
    item.setData(role, kept)
    return before != after


def chart(root):
    import pandas as pd
    from src.desktop.case_page import GLYPH_ROLE, SEVERITY_ROLE
    from src.desktop.charts import FocusedSignalChart, focused_signals_figure
    from src.rules import load_rule_catalog
    from src.services.case_fetch import FetchResult
    from src.services.retrieval import RetrievalResult, SearchCandidate

    app = qt()
    ts = pd.concat([frames("SYN_A")[1]] * 3, ignore_index=True)
    ts["t_min"] = [0., 1., 2.]
    ts["Obs_time"] = pd.date_range("2016-01-01", periods=3, freq="min")
    # One rule per severity, so each severity is its own episode in the lane and in the list.
    flags = pd.DataFrame({"PID": "SYN_A", "t_min": [0., 1., 2.], "severity": ["info", "warn", "critical"],
                          "rule_id": ["pip_rising", "etco2_high", "spo2_low"], "value": 1., "message": "synthetic",
                          "phase": "maintenance", "Obs_time": ts["Obs_time"]})
    widget = FocusedSignalChart()
    widget.set_case(ts, flags)
    figure = focused_signals_figure(ts, None, flags=flags)
    figure.savefig(root / "severity.png")
    lane = figure.axes[-1]
    shapes = [f"{line.get_label()}:{line.get_marker()}:{line.get_fillstyle()}" for line in lane.lines
              if line.get_label() in {"info", "warn", "critical"}]
    win = window()
    catalog = load_rule_catalog("default")
    win.research.set_rule_catalog(catalog)
    candidate = SearchCandidate("SYN_A", "analyzed case", True, "Synthetic procedure", 55, "F", "PID SYN_A.", "keyword", 0.5)
    win.research.set_results(RetrievalResult(candidates=(candidate,)))
    fetched = FetchResult(cases=pd.DataFrame({"PID": ["SYN_A"], "Age": [55]}), timeseries=ts, flags=flags, events=pd.DataFrame())
    win.research.set_case_data("SYN_A", ts, flags, fetched=fetched, thresholds_sha256=catalog.thresholds_sha256)
    win.show()
    win.research.open_detail("SYN_A")
    pump(app)
    episodes = win.research.episode_list
    rows = []
    for index in range(episodes.count()):
        item = episodes.item(index)
        in_view = episodes.isVisible() and episodes.viewport().rect().contains(episodes.visualItemRect(item))
        rows.append("|".join([str(item.data(SEVERITY_ROLE)), str(item.data(GLYPH_ROLE)), item.text(),
                              str(int(painted(episodes, item, GLYPH_ROLE))),
                              str(int(painted(episodes, item, SEVERITY_ROLE))), str(int(in_view))]))
    result = {"name": accessible_name(widget), "description": widget.accessibleDescription(),
              "lane_markers": "\n".join(shapes), "lane_hatched_bands": sum(bool(patch.get_hatch()) for patch in lane.patches),
              "episode_rows": "\n".join(rows), "episode_list_name": accessible_name(episodes)}
    widget.close()
    win.close()
    app.processEvents()
    return result


def layout(root):
    from PySide6.QtCore import QPoint
    from src.desktop.settings_page import SettingsPage
    from src.desktop.setup_flow import DATA, SetupFlow

    app = qt()
    with no_ollama():
        settings, wizard = SettingsPage(), SetupFlow("0")
    wizard.resize(1000, 700)
    wizard.show()
    wizard._go(DATA)
    settings.resize(900, 640)
    settings.show()
    win = window()
    win.research.set_setup_required(False)
    win.show()
    pump(app)

    def geometry(parent, widget):
        position = widget.mapTo(parent, QPoint(0, 0))
        return ",".join(map(str, [parent.width(), parent.height(), position.x(), position.y(), widget.width(), widget.height(), int(widget.isVisible())]))

    wizard_buttons = [wizard.back_button, wizard.continue_button, wizard.not_now_data]
    settings_buttons = [settings.discard_button, settings.save_button]
    card = win.research.composer
    result = {"wizard_navigation": "\n".join(geometry(wizard, button) for button in wizard_buttons),
              "settings_navigation": "\n".join(geometry(settings, button) for button in settings_buttons),
              "question_card": geometry(win.research.conversation, card),
              "screen_geometry": str(app.primaryScreen().availableGeometry().getRect()),
              "window_geometry": str(win.geometry().getRect())}
    for label, widget in [("main", win), ("settings", settings), ("wizard", wizard)]:
        widget.grab().save(str(root / f"{label}.png"))
        widget.close()
    return result


def line_is_whole(head, label):
    """True when the head's line is shown with every letter: visible, inside the head, as wide as its text."""
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QFontMetrics

    left = label.mapTo(head, QPoint(0, 0)).x()
    return (label.isVisible() and left >= 0 and left + label.width() <= head.width()
            and QFontMetrics(label.font()).horizontalAdvance(label.text()) <= label.width())


def startup_layout(root):
    from PySide6.QtWidgets import QApplication
    from src.desktop.app import MainWindow, main as desktop_main
    from src.desktop.shell import ACTIONS, PLACES

    app = qt()
    result = {}

    def observe(_app):
        from PySide6.QtCore import QPoint

        pump(app)
        win = next(widget for widget in app.topLevelWidgets() if isinstance(widget, MainWindow))
        result.update(screen_width=app.primaryScreen().availableGeometry().width(),
                      screen_height=app.primaryScreen().availableGeometry().height(),
                      window_width=win.width(), window_height=win.height(),
                      board_height=win.research.board_scroll.viewport().height())
        viewport = win.research.board_scroll.viewport()
        research = win.research
        # Clear board and Add comparison sit in the Evidence header. Scope, AI and Send sit on the composer.
        header = [("Clear board", research.new_question_button), ("Add comparison", research.add_comparison_button)]
        result["header_action_names"] = "\n".join(name for name, widget in header if widget.isVisible())
        result["header_actions"] = "\n".join(
            f"{widget.mapTo(viewport, QPoint(0, 0)).x()},{widget.width()},{viewport.width()}"
            for _name, widget in header if widget.isVisible()
        )
        conversation = win.research.conversation
        composer_bar = research.composer.parentWidget()
        composer_row = [("Question scope", research.scope_switch), ("AI", research.model_menu),
                        ("Trace the evidence", research.ask_button)]
        result["composer_action_names"] = "\n".join(name for name, widget in composer_row if widget.isVisible())
        result["composer_actions"] = "\n".join(
            f"{widget.mapTo(composer_bar, QPoint(0, 0)).x()},{widget.width()},{composer_bar.width()}"
            for _name, widget in composer_row if widget.isVisible()
        )
        result["thread_names"] = "\n".join((
            research.composer.objectName(),
            research.composer.accessibleName(),
            "turnBubble",
            "foldToggle",
            conversation.reset_button.accessibleName(),
        ))
        reset = conversation.reset_button
        result["reset_action"] = (f"{reset.mapTo(conversation, QPoint(0, 0)).x()},{reset.width()},"
                                  f"{conversation.width()},{int(reset.isVisible())}")
        # A sideways scroll bar under the board means something is wider than the board.
        result["board_sideways_scrollbar"] = win.research.board_scroll.horizontalScrollBar().isVisible()
        result["research_line"] = win.head.stamp.text()
        result["research_line_whole"] = line_is_whole(win.head, win.head.stamp)
        win.rail.button("practice").click()
        pump(app)
        result["practice_line"] = win.head.fiction.text()
        result["practice_line_whole"] = line_is_whole(win.head, win.head.fiction)
        result["practice_window_width"] = win.width()
        win.rail.button("research").click()
        pump(app)
        rail = [win.rail.button(key) for key, _label, _icon in [*PLACES, *ACTIONS]]
        result["rail_buttons"] = "\n".join(
            f"{button.mapTo(win.rail, QPoint(0, 0)).y()},{button.height()},{win.rail.height()},{int(button.isVisible())}"
            for button in rail
        )
        win.grab().save(str(root / "startup.png"))
        win.close()
        return 0

    with patch.object(MainWindow, "_startup", lambda self: None), patch.object(QApplication, "exec", observe):
        desktop_main()
    return result


def main():
    operation, variant, directory = sys.argv[1:]
    root = Path(directory)
    if operation == "prepare":
        result = prepare(root)
    elif operation in {"pipeline", "scan", "settings"}:
        result = output_route(root, operation, variant)
    elif operation == "default_output":
        from src.runtime_paths import default_processed_for_emr

        source = root / "custom-export"
        result = {"source": str(source), "output": str(default_processed_for_emr(source))}
    else:
        operations = {"provenance": lambda: provenance(root, variant), "switch": lambda: switch_source(root),
                      "error": lambda: errors(root, variant), "announcements": lambda: announcements(root),
                      "names": lambda: names(root), "focus": lambda: focus(root), "chart": lambda: chart(root),
                      "layout": lambda: layout(root), "startup_layout": lambda: startup_layout(root)}
        result = operations[operation]()
    emit("protocol", 1)
    for key, value in result.items():
        emit(key, value)


if __name__ == "__main__":
    main()
