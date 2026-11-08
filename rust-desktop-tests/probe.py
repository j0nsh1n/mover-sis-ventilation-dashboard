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
        from src.desktop.settings_ui import PathsForm

        app = qt()
        with no_ollama():
            form = PathsForm()
        form.edit_emr.setText(str(source))
        form.edit_processed.setText(str(output))
        form.edit_wave.clear()
        form.edit_ollama_models.clear()
        def action():
            return form.apply_to_runtime(require_emr=True)
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

    with patch.object(desktop, "DataLoadWorker", ScheduledLoad), patch.object(QMessageBox, "open", lambda box: dialogs.append(box.text())):
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
        with patch.object(QMessageBox, "open", lambda box: messages.append(box.text())):
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
    from PySide6.QtGui import QAccessible

    app = qt()
    events = []
    with patch.object(QAccessible, "updateAccessibility", lambda event: events.append((event.type().name, event.message()))):
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
    from src.desktop.settings_ui import PathsForm

    app = qt()
    with no_ollama():
        form = PathsForm()
    win = window()
    result = {key: accessible_name(widget) for key, widget in [
        ("emr", form.edit_emr), ("wave", form.edit_wave), ("processed", form.edit_processed),
        ("models", form.edit_ollama_models), ("question", win.research.question_edit)]}
    for label in ["Cases", "New question", "Reset", "Rules"]:
        buttons = [button for button in win.findChildren(QPushButton) if button.text() == label]
        result[label.replace(" ", "_").lower()] = accessible_name(buttons[0]) if buttons else ""
    result["table"] = "\n".join(accessible_name(table) for table in win.findChildren(QTableWidget))
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

    app = qt()
    win = window()
    win.research.set_setup_required(False)
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
    for key, widget in [("combo", win.research.model_combo), ("table", win.research.candidate_table)]:
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


def chart(root):
    import pandas as pd
    from PySide6.QtWidgets import QLabel
    from src.desktop.charts import FocusedSignalChart, focused_signals_figure

    app = qt()
    ts = pd.concat([frames("SYN_A")[1]] * 3, ignore_index=True)
    ts["t_min"] = [0., 1., 2.]
    ts["Obs_time"] = pd.date_range("2016-01-01", periods=3, freq="min")
    flags = pd.DataFrame({"t_min": [0., 1., 2.], "severity": ["info", "warn", "critical"]})
    widget = FocusedSignalChart()
    widget.set_case(ts, flags)
    figure = focused_signals_figure(ts, None, flags=flags)
    figure.savefig(root / "severity.png")
    paths = [repr(collection.get_paths()[0].vertices.tolist()) for collection in figure.axes[-1].collections]
    win = window()
    win.show()
    win.research.stack.setCurrentIndex(1)
    pump(app)
    legend = next((label for label in win.findChildren(QLabel) if "Flag marks" in label.text()), None)
    result = {"name": accessible_name(widget), "description": widget.accessibleDescription(),
              "marker_paths": "\n".join(paths),
              "legend": legend.text() if legend else "", "legend_visible": legend.isVisible() if legend else False}
    widget.close()
    win.close()
    app.processEvents()
    return result


def layout(root):
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QDialogButtonBox, QWizard
    from src.desktop.settings_ui import SettingsDialog, SetupWizard

    app = qt()
    with no_ollama():
        settings, wizard = SettingsDialog(), SetupWizard()
    wizard.show()
    wizard.next()
    settings.show()
    win = window()
    win.research.set_setup_required(False)
    win.show()
    pump(app)

    def geometry(parent, widget):
        position = widget.mapTo(parent, QPoint(0, 0))
        return ",".join(map(str, [parent.width(), parent.height(), position.x(), position.y(), widget.width(), widget.height(), int(widget.isVisible())]))

    wizard_buttons = [wizard.button(kind) for kind in [QWizard.WizardButton.BackButton, QWizard.WizardButton.NextButton, QWizard.WizardButton.CancelButton]]
    settings_buttons = settings.findChild(QDialogButtonBox).buttons()
    card = win.research.question_edit.parentWidget()
    result = {"wizard_navigation": "\n".join(geometry(wizard, button) for button in wizard_buttons),
              "settings_navigation": "\n".join(geometry(settings, button) for button in settings_buttons),
              "question_card": geometry(win.research.board_scroll.viewport(), card),
              "screen_geometry": str(app.primaryScreen().availableGeometry().getRect()),
              "window_geometry": str(win.geometry().getRect())}
    for label, widget in [("main", win), ("settings", settings), ("wizard", wizard)]:
        widget.grab().save(str(root / f"{label}.png"))
        widget.close()
    return result


def startup_layout(root):
    from PySide6.QtWidgets import QApplication
    from src.desktop.app import MainWindow, main as desktop_main

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
        result["header_actions"] = "\n".join(
            f"{button.mapTo(viewport, QPoint(0, 0)).x()},{button.width()},{viewport.width()}"
            for button in win.research._header_buttons
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
