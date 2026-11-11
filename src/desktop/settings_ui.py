"""Parts shared by the Settings page and the first-time setup flow.

Both screens edit the same saved values, check the same folders and write through the same
function, so a folder that passes in one passes in the other. The screens themselves are in
``settings_page.py`` and ``setup_flow.py``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QMessageBox,
    QWizard,
    QWizardPage,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.guardrails.limits import ALLOWED_PRESETS, REQUIRED_EMR_FILES
from src.desktop.scroll_safe_combo import ScrollSafeComboBox
from src.guardrails.validate_io import validate_emr_dir
from src.runtime_paths import (
    configure_emr_directory,
    configure_processed_directory,
    configure_wave_directory,
    emr_dir,
    processed_dir,
    resolve_emr_directory,
    resolve_wave_directory,
    validate_output_location,
    wave_dir,
)
from src.user_settings import (
    THEME_CHOICES,
    THEME_DARK,
    THEME_LIGHT,
    THEME_SYSTEM,
    apply_ollama_env_from_settings,
    detect_default_ollama_models_dir,
    get_theme,
    load_settings,
    mark_setup_complete,
    update_settings,
)

# What the page and the leave prompt call each saved value, in the order they are listed.
CHANGE_LABELS = {
    "emr_dir": "EMR folder",
    "processed_dir": "processed folder",
    "wave_dir": "wave folder",
    "preset": "rule preset",
    "ollama_models_dir": "models folder",
    "ollama_model": "preferred model",
    "ollama_embed_model": "embedding model",
    "theme": "theme",
}
# Reading the first column of a table to count its rows stays fast below this size; above it the count is left out.
_COUNT_LIMIT_BYTES = 64 * 1024 * 1024


def browse_dir(parent: QWidget, start: str = "") -> str:
    path = QFileDialog.getExistingDirectory(parent, "Select folder", start or str(Path.home()))
    return path or ""


def scroll_panel(content: QWidget) -> QScrollArea:
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    scroll.setWidget(content)
    return scroll


def saved_values() -> dict[str, str]:
    """The values the app is using now, with the fallbacks the old Settings dialog used."""
    cfg = load_settings()
    preset = cfg.get("preset")
    try:
        emr = cfg.get("emr_dir") or str(emr_dir())
    except Exception:
        emr = str(cfg.get("emr_dir") or "")
    wave = cfg.get("wave_dir")
    if not wave:
        try:
            current = wave_dir()
            wave = str(current) if current else ""
        except Exception:
            wave = ""
    try:
        processed = cfg.get("processed_dir") or str(processed_dir())
    except Exception:
        processed = str(cfg.get("processed_dir") or "")
    theme = get_theme()
    return {
        "emr_dir": emr,
        "wave_dir": wave or "",
        "processed_dir": processed,
        "ollama_models_dir": cfg.get("ollama_models_dir") or detect_default_ollama_models_dir() or "",
        "ollama_model": str(cfg.get("ollama_model") or ""),
        "ollama_embed_model": str(cfg.get("ollama_embed_model") or "nomic-embed-text"),
        "theme": theme if theme in THEME_CHOICES else THEME_SYSTEM,
        "preset": preset if preset in ALLOWED_PRESETS else "default",
    }


def changed_labels(before: dict[str, str], after: dict[str, str]) -> list[str]:
    """The names of the values that differ, in list order."""
    return [label for key, label in CHANGE_LABELS.items() if before.get(key, "") != after.get(key, "")]


def validate_values(vals: dict[str, str], *, require_emr: bool = False) -> list[str]:
    """Everything that would make ``apply_values`` refuse, found before anything is written."""
    errors: list[str] = []
    emr_for_output: Path | None = None
    if vals["emr_dir"]:
        try:
            emr_for_output = resolve_emr_directory(vals["emr_dir"])
        except Exception as e:
            errors.append(f"EMR: {e}")
    elif require_emr:
        errors.append("EMR folder is required for setup.")
    if vals["wave_dir"]:
        try:
            resolve_wave_directory(vals["wave_dir"])
        except Exception as e:
            errors.append(f"Wave: {e}")
    if vals["processed_dir"]:
        try:
            validate_output_location(vals["processed_dir"], source_emr=emr_for_output)
        except Exception as e:
            errors.append(f"Processed: {e}")
    models = vals["ollama_models_dir"]
    if models and not Path(models).expanduser().is_dir():
        errors.append(f"Ollama models directory does not exist: {Path(models).expanduser()}")
    return errors


def apply_values(vals: dict[str, str], *, require_emr: bool = False) -> list[str]:
    """Persist the values and apply paths and the models folder to this session.

    Returns non-fatal warnings. Raises ``ValueError`` naming every value that was refused,
    before anything is written, so a refused save changes nothing.
    """
    errors = validate_values(vals, require_emr=require_emr)
    if errors:
        raise ValueError("\n".join(errors))
    warnings: list[str] = []
    if vals["emr_dir"]:
        configure_emr_directory(vals["emr_dir"], persist=True)
    if vals["wave_dir"]:
        configure_wave_directory(vals["wave_dir"], persist=True)
    else:
        update_settings(wave_dir=None)
    if vals["processed_dir"]:
        configure_processed_directory(vals["processed_dir"], persist=True)
    models = vals["ollama_models_dir"]
    if models:
        update_settings(ollama_models_dir=str(Path(models).expanduser().resolve()))
    else:
        update_settings(ollama_models_dir=None)
    apply_ollama_env_from_settings()
    update_settings(ollama_model=vals["ollama_model"] or None)
    update_settings(ollama_embed_model=vals["ollama_embed_model"] or "nomic-embed-text")
    update_settings(theme=vals["theme"])
    update_settings(preset=vals["preset"])
    return warnings


def check_models_folder(text: str) -> FolderCheck:
    text = text.strip()
    if not text:
        return FolderCheck("note", "Not set. Ollama uses its own default folder.")
    if Path(text).expanduser().is_dir():
        return FolderCheck("ok", "Folder found.")
    return FolderCheck("problem", "This folder does not exist.")


def probe_models(embedding_model: str) -> tuple[list[str], str]:
    """The models a running Ollama lists, and one sentence on the embedding model. Nothing here starts Ollama."""
    try:
        from src.llm.service import ensure_ollama

        apply_ollama_env_from_settings()
        status = ensure_ollama(start_if_needed=False, wait_s=0.5)
        models = list(status.models)
        available = any(m.split(":")[0] == embedding_model.split(":")[0] for m in models)
        return models, ("Embedding model ready" if available
                        else "Embedding model unavailable · case search will use keywords")
    except Exception:
        return [], "Ollama unavailable · case search will use keywords"


@dataclass(frozen=True)
class FolderCheck:
    """What a folder holds, in one sentence. ``state`` is ok, problem or note; the words carry it too."""

    state: str
    text: str


def _now() -> str:
    return datetime.now().strftime("%H:%M")


def _count_surgeries(info: Path) -> int | None:
    try:
        if info.stat().st_size > _COUNT_LIMIT_BYTES:
            return None
        import pandas as pd

        return int(len(pd.read_csv(info, usecols=[0], dtype=str)))
    except Exception:
        return None


def check_emr_folder(text: str) -> FolderCheck:
    text = text.strip()
    if not text:
        return FolderCheck("note", "No folder chosen yet.")
    try:
        found = resolve_emr_directory(text)
    except FileNotFoundError as exc:
        return FolderCheck("problem", f"Not usable: {exc}")
    present = [name for name in REQUIRED_EMR_FILES if (found / name).is_file() and (found / name).stat().st_size > 0]
    missing = [name for name in REQUIRED_EMR_FILES if name not in present]
    tables = f"{len(present)} of {len(REQUIRED_EMR_FILES)} required tables found"
    if missing:
        return FolderCheck("problem", f"{tables} · missing or empty: {', '.join(missing)}")
    surgeries = _count_surgeries(found / REQUIRED_EMR_FILES[0])
    counted = f"{surgeries:,} surgeries indexed · " if surgeries is not None else ""
    return FolderCheck("ok", f"{counted}{tables} · checked {_now()}")


def check_processed_folder(text: str) -> FolderCheck:
    text = text.strip()
    if not text:
        return FolderCheck("note", "No folder chosen yet.")
    folder = Path(text).expanduser()
    cases = folder / "cases.parquet"
    if not cases.is_file():
        where = "This folder does not exist yet; it is made when you save. " if not folder.is_dir() else ""
        return FolderCheck(
            "note",
            f"{where}Nothing analyzed here yet. Search over indexed surgeries still works; "
            "press Ctrl+K and choose Run pipeline to analyze a sample.",
        )
    try:
        import pyarrow.parquet as pq

        count = pq.ParquetFile(cases).metadata.num_rows
    except Exception as exc:
        return FolderCheck("problem", f"cases.parquet could not be read ({type(exc).__name__}).")
    preset = ""
    try:
        meta = json.loads((folder / "run_meta.json").read_text(encoding="utf-8"))
        name = meta.get("preset") if isinstance(meta, dict) else None
        sha = meta.get("thresholds_sha256") if isinstance(meta, dict) else None
        if isinstance(name, str) and name:
            preset = f" with preset {name}" + (f" @{sha[:6]}" if isinstance(sha, str) and sha else "")
    except (OSError, ValueError):
        pass
    return FolderCheck("ok", f"{count:,} cases analyzed{preset} · checked {_now()}")


def check_wave_folder(text: str) -> FolderCheck:
    text = text.strip()
    if not text:
        return FolderCheck("note", "Nothing to check.")
    try:
        resolve_wave_directory(text)
    except FileNotFoundError as exc:
        return FolderCheck("problem", f"Not usable: {exc}")
    return FolderCheck("ok", f"Waveform data found · checked {_now()}")


class SettingRow(QWidget):
    """One setting: its name and what it is for on the left, the control and a note on the right.

    Wide, the name sits left of the control; narrow, it sits above it.
    """

    STACK_BELOW = 640
    LEFT_COLUMN = 250

    def __init__(self, label: str, tag: str, purpose: str, parent=None):
        super().__init__(parent)
        self._stacked: bool | None = None
        self.title = QLabel(label)
        self.title.setObjectName("settingName")
        self.tag = QLabel(tag)
        self.tag.setObjectName("quietText")
        self.tag.setVisible(bool(tag))
        names = QWidget()
        name_row = QHBoxLayout(names)
        name_row.setContentsMargins(0, 0, 0, 0)
        name_row.setSpacing(6)
        name_row.addWidget(self.title)
        name_row.addWidget(self.tag)
        name_row.addStretch(1)
        self.purpose = QLabel(purpose)
        self.purpose.setObjectName("quietText")
        self.purpose.setWordWrap(True)
        self.note = QLabel()
        self.note.setObjectName("folderCheck")
        self.note.setWordWrap(True)
        self.note.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.note.hide()
        self._left = QWidget()
        left = QGridLayout(self._left)
        left.setContentsMargins(0, 0, 0, 0)
        left.setVerticalSpacing(4)
        left.addWidget(names, 0, 0)
        left.addWidget(self.purpose, 1, 0)
        left.setRowStretch(2, 1)
        self._field = QWidget()
        self.field_layout = QVBoxLayout(self._field)
        self.field_layout.setContentsMargins(0, 0, 0, 0)
        self.field_layout.setSpacing(8)
        self.field_layout.addWidget(self.note)
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(32)
        self._grid.setVerticalSpacing(8)
        self._reflow(1000)

    def add_control(self, widget: QWidget, *, left: bool = False) -> None:
        """Put ``widget`` above the note; ``left`` keeps a button at its own width instead of the row's."""
        self.field_layout.insertWidget(self.field_layout.count() - 1, widget, 0,
                                       Qt.AlignmentFlag.AlignLeft if left else Qt.AlignmentFlag(0))

    def set_note(self, text: str, state: str = "note") -> None:
        self.note.setText(text)
        self.note.setProperty("state", state)
        self.note.style().unpolish(self.note)
        self.note.style().polish(self.note)
        self.note.setVisible(bool(text))

    def _reflow(self, width: int) -> None:
        stacked = width < self.STACK_BELOW
        if stacked == self._stacked:
            return
        self._stacked = stacked
        self._grid.removeWidget(self._left)
        self._grid.removeWidget(self._field)
        if stacked:
            self._grid.addWidget(self._left, 0, 0)
            self._grid.addWidget(self._field, 1, 0)
            self._left.setMinimumWidth(0)
            self._left.setMaximumWidth(16777215)
            self._grid.setColumnMinimumWidth(0, 0)
            self._grid.setColumnStretch(0, 1)
            self._grid.setColumnStretch(1, 0)
        else:
            self._grid.addWidget(self._left, 0, 0)
            self._grid.addWidget(self._field, 0, 1)
            self._left.setFixedWidth(self.LEFT_COLUMN)
            self._grid.setColumnMinimumWidth(0, self.LEFT_COLUMN)
            self._grid.setColumnStretch(0, 0)
            self._grid.setColumnStretch(1, 1)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._reflow(self.width())


class FolderRow(SettingRow):
    """A folder setting: the path, Choose and a one-sentence check.

    The check runs when a folder is chosen or the field loses focus, never on each keystroke.
    """

    changed = Signal()
    # Emitted after the folder was checked, whether the person typed it or chose it
    checked = Signal()

    def __init__(self, label: str, tag: str, purpose: str, placeholder: str, checker, parent=None):
        super().__init__(label, tag, purpose, parent)
        self._checker = checker
        # True once the person typed or chose a folder here, so a default derived from another field stops following
        self.touched = False
        self.edit = QLineEdit()
        self.edit.setPlaceholderText(placeholder)
        self.edit.setClearButtonEnabled(True)
        self.edit.setAccessibleName(label)
        self.title.setBuddy(self.edit)
        self.choose_button = QPushButton("Choose…")
        self.choose_button.setAccessibleName(f"Choose {label.lower()}")
        self.choose_button.clicked.connect(self._choose)
        controls = QWidget()
        row = QHBoxLayout(controls)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        row.addWidget(self.edit, 1)
        row.addWidget(self.choose_button)
        self.add_control(controls)
        self.edit.textChanged.connect(lambda _text: self.changed.emit())
        self.edit.textEdited.connect(lambda _text: setattr(self, "touched", True))
        self.edit.editingFinished.connect(self.run_check)

    def text(self) -> str:
        return self.edit.text().strip()

    def set_text(self, text: str) -> None:
        """Show ``text`` and check it. This is a load, not an edit, so callers decide whether it counts as one."""
        self.edit.setText(text)
        self.run_check()

    def run_check(self) -> FolderCheck:
        result = self._checker(self.edit.text())
        glyph = {"ok": "✓ ", "problem": "✕ ", "note": ""}[result.state]
        self.set_note(glyph + result.text, result.state)
        self.note.setAccessibleName(f"{self.edit.accessibleName()} check")
        self.note.setAccessibleDescription(result.text)
        self.checked.emit()
        return result

    def _choose(self) -> None:
        chosen = browse_dir(self, self.edit.text())
        if chosen:
            self.touched = True
            self.edit.setText(chosen)
            self.run_check()


def _size_dialog(dialog: QDialog, width: int, height: int, minimum_width: int) -> None:
    screen = dialog.screen()
    available = screen.availableGeometry() if screen is not None else None
    dialog.setMinimumWidth(min(minimum_width, available.width()) if available else minimum_width)
    dialog.resize(min(width, available.width()) if available else width,
                  min(height, available.height()) if available else height)


class PathsForm(QWidget):
    """Shared path + LLM + theme form used by Settings and Setup."""

    def __init__(self, parent=None, *, compact: bool = False):
        super().__init__(parent)
        self._compact = compact
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        # --- Data ---
        data_box = QGroupBox("Data locations")
        dl = QVBoxLayout(data_box)
        if not compact:
            hint = QLabel(
                "EMR = tabular SIS tables (patient_*.csv). "
                "Wave = waveform archives (optional). "
                "Processed = parquet cache written by the pipeline."
            )
            hint.setObjectName("pathHint")
            hint.setWordWrap(True)
            dl.addWidget(hint)

        self.edit_emr = QLineEdit()
        self.edit_emr.setPlaceholderText("Folder with patient_information.csv")
        self.edit_emr.setClearButtonEnabled(True)
        dl.addLayout(self._row("EMR folder", self.edit_emr, self._browse_emr))

        self.edit_wave = QLineEdit()
        self.edit_wave.setPlaceholderText("Optional: Waveforms/ or sis_wave*.tar.gz")
        self.edit_wave.setClearButtonEnabled(True)
        wave_row = self._row("Wave folder", self.edit_wave, self._browse_wave)
        if not compact:
            dl.addLayout(wave_row)

        self.edit_processed = QLineEdit()
        self.edit_processed.setPlaceholderText("Parquet cache (often next to EMR)")
        self.edit_processed.setClearButtonEnabled(True)
        dl.addLayout(
            self._row("Processed folder", self.edit_processed, self._browse_processed)
        )
        root.addWidget(data_box)
        if compact:
            advanced = QGroupBox("Advanced · optional wave data")
            QVBoxLayout(advanced).addLayout(wave_row)
            root.addWidget(advanced)

        run_form = QFormLayout()
        self.combo_preset = ScrollSafeComboBox()
        for label, preset in (("Default", "default"), ("Lenient", "lenient"), ("Strict", "strict")):
            self.combo_preset.addItem(label, preset)
        run_form.addRow("Rule preset for new runs", self.combo_preset)
        dl.addLayout(run_form)
        preset_hint = QLabel(
            "Loaded data keeps the preset it was computed with. "
            "Run the pipeline again to apply a new preset."
        )
        preset_hint.setObjectName("pathHint")
        preset_hint.setWordWrap(True)
        dl.addWidget(preset_hint)

        # --- LLM ---
        llm_box = QGroupBox("Local LLM (Ollama)")
        ll = QVBoxLayout(llm_box)
        if not compact:
            lh = QLabel(
                "Models stay on disk (not inside the app). "
                "Set the Ollama models directory if it is not the default "
                "(e.g. /var/mnt/games/LLM_Models). The app sets OLLAMA_MODELS "
                "when starting ollama serve."
            )
            lh.setObjectName("pathHint")
            lh.setWordWrap(True)
            ll.addWidget(lh)

        self.edit_ollama_models = QLineEdit()
        self.edit_ollama_models.setPlaceholderText("OLLAMA_MODELS path")
        self.edit_ollama_models.setClearButtonEnabled(True)
        ll.addLayout(
            self._row(
                "Models directory",
                self.edit_ollama_models,
                self._browse_ollama_models,
            )
        )

        form = QFormLayout()
        self.combo_default_model = ScrollSafeComboBox()
        self.combo_default_model.setEditable(True)
        self.combo_default_model.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        form.addRow("Preferred model", self.combo_default_model)
        ll.addLayout(form)
        self.edit_embed_model = QLineEdit()
        self.edit_embed_model.setPlaceholderText("nomic-embed-text")
        form.addRow("Embedding model", self.edit_embed_model)
        self.lbl_embed_ready = QLabel()
        self.lbl_embed_ready.setWordWrap(True)
        ll.addWidget(self.lbl_embed_ready)
        root.addWidget(llm_box)

        # --- Appearance ---
        theme_box = QGroupBox("Appearance")
        tf = QFormLayout(theme_box)
        self.combo_theme = ScrollSafeComboBox()
        self.combo_theme.addItem("Light", THEME_LIGHT)
        self.combo_theme.addItem("Dark", THEME_DARK)
        self.combo_theme.addItem("System (follow OS)", THEME_SYSTEM)
        tf.addRow("Theme", self.combo_theme)
        root.addWidget(theme_box)

        root.addStretch(1)
        self.load_from_settings()

    def _row(self, label: str, edit: QLineEdit, browse_slot) -> QVBoxLayout:
        wrap = QVBoxLayout()
        wrap.setSpacing(2)
        field_label = QLabel(label)
        field_label.setBuddy(edit)
        edit.setAccessibleName(label)
        wrap.addWidget(field_label)
        row = QHBoxLayout()
        row.addWidget(edit, stretch=1)
        btn = QPushButton("Browse…")
        btn.setObjectName("secondaryBtn")
        btn.clicked.connect(browse_slot)
        row.addWidget(btn)
        wrap.addLayout(row)
        return wrap

    def _browse_emr(self) -> None:
        p = browse_dir(self, self.edit_emr.text())
        if p:
            self.edit_emr.setText(p)

    def _browse_wave(self) -> None:
        p = browse_dir(self, self.edit_wave.text())
        if p:
            self.edit_wave.setText(p)

    def _browse_processed(self) -> None:
        p = browse_dir(self, self.edit_processed.text())
        if p:
            self.edit_processed.setText(p)

    def _browse_ollama_models(self) -> None:
        p = browse_dir(self, self.edit_ollama_models.text())
        if p:
            self.edit_ollama_models.setText(p)

    def load_from_settings(self) -> None:
        cfg = load_settings()
        preset_index = self.combo_preset.findData(cfg.get("preset"))
        self.combo_preset.setCurrentIndex(max(0, preset_index))
        try:
            self.edit_emr.setText(cfg.get("emr_dir") or str(emr_dir()))
        except Exception:
            self.edit_emr.setText(str(cfg.get("emr_dir") or ""))
        w = cfg.get("wave_dir")
        if not w:
            try:
                wd = wave_dir()
                w = str(wd) if wd else ""
            except Exception:
                w = ""
        self.edit_wave.setText(w or "")
        try:
            self.edit_processed.setText(cfg.get("processed_dir") or str(processed_dir()))
        except Exception:
            self.edit_processed.setText(str(cfg.get("processed_dir") or ""))

        models = cfg.get("ollama_models_dir") or detect_default_ollama_models_dir() or ""
        self.edit_ollama_models.setText(models)

        theme = get_theme()
        idx = self.combo_theme.findData(theme)
        self.combo_theme.setCurrentIndex(idx if idx >= 0 else self.combo_theme.findData(THEME_SYSTEM))

        preferred = str(cfg.get("ollama_model") or "")
        embedding_model = str(cfg.get("ollama_embed_model") or "nomic-embed-text")
        self.edit_embed_model.setText(embedding_model)
        self.combo_default_model.clear()
        self.combo_default_model.addItem("")
        # Best-effort model list
        try:
            from src.llm.service import ensure_ollama

            apply_ollama_env_from_settings()
            st = ensure_ollama(start_if_needed=False, wait_s=0.5)
            for m in st.models:
                self.combo_default_model.addItem(m)
            available = any(m.split(":")[0] == embedding_model.split(":")[0] for m in st.models)
            self.lbl_embed_ready.setText(
                "Embedding model ready" if available else "Embedding model unavailable · case search will use keywords"
            )
        except Exception:
            self.lbl_embed_ready.setText("Ollama unavailable · case search will use keywords")
        if preferred:
            i = self.combo_default_model.findText(preferred)
            if i < 0:
                self.combo_default_model.addItem(preferred)
                i = self.combo_default_model.findText(preferred)
            self.combo_default_model.setCurrentIndex(max(0, i))

    def collect(self) -> dict:
        theme_data = self.combo_theme.currentData()
        theme = theme_data if theme_data in THEME_CHOICES else THEME_SYSTEM
        return {
            "emr_dir": self.edit_emr.text().strip(),
            "wave_dir": self.edit_wave.text().strip(),
            "processed_dir": self.edit_processed.text().strip(),
            "ollama_models_dir": self.edit_ollama_models.text().strip(),
            "ollama_model": self.combo_default_model.currentText().strip(),
            "ollama_embed_model": self.edit_embed_model.text().strip() or "nomic-embed-text",
            "theme": theme,
            "preset": self.combo_preset.currentData(),
        }

    def apply_to_runtime(self, *, require_emr: bool = False) -> list[str]:
        """
        Persist fields and apply path/LLM env.

        Returns list of non-fatal warning messages.
        """
        vals = self.collect()
        errors: list[str] = []
        warnings: list[str] = []

        if vals["emr_dir"]:
            try:
                configure_emr_directory(vals["emr_dir"], persist=True)
            except Exception as e:
                errors.append(f"EMR: {e}")
        elif require_emr:
            errors.append("EMR folder is required for setup.")

        if vals["wave_dir"]:
            try:
                configure_wave_directory(vals["wave_dir"], persist=True)
            except Exception as e:
                errors.append(f"Wave: {e}")
        else:
            update_settings(wave_dir=None)

        if vals["processed_dir"]:
            try:
                configure_processed_directory(vals["processed_dir"], persist=True)
            except Exception as e:
                errors.append(f"Processed: {e}")

        models = vals["ollama_models_dir"]
        if models:
            p = Path(models).expanduser()
            if not p.is_dir():
                errors.append(f"Ollama models directory does not exist: {p}")
            else:
                update_settings(ollama_models_dir=str(p.resolve()))
                apply_ollama_env_from_settings()
        else:
            update_settings(ollama_models_dir=None)
            apply_ollama_env_from_settings()

        if vals["ollama_model"]:
            update_settings(ollama_model=vals["ollama_model"])
        else:
            update_settings(ollama_model=None)
        update_settings(ollama_embed_model=vals["ollama_embed_model"])

        update_settings(theme=vals["theme"])
        update_settings(preset=vals["preset"])

        if errors:
            raise ValueError("\n".join(errors))
        return warnings




class _WelcomePage(QWizardPage):
    def __init__(self):
        super().__init__()
        self.setTitle("Welcome")
        self.setSubTitle(
            "Configure data folders, local LLM models, and appearance. "
            "You can change everything later under Settings."
        )
        lay = QVBoxLayout(self)
        body = QLabel(
            "<p><b>MOVER Suite</b> is a research desktop app for "
            "perioperative SIS data (not for clinical care).</p>"
            "<p>On the next pages you will set:</p>"
            "<ul>"
            "<li><b>EMR</b> folder (required) — patient_*.csv tables</li>"
            "<li><b>Wave</b> folder (optional) — waveform archives</li>"
            "<li><b>Processed</b> cache and <b>Ollama models</b> directory</li>"
            "<li><b>Theme</b> — light, dark, or system</li>"
            "</ul>"
        )
        body.setWordWrap(True)
        body.setTextFormat(Qt.TextFormat.RichText)
        body.setAlignment(Qt.AlignmentFlag.AlignTop)
        lay.addWidget(scroll_panel(body))


class _ConfigPage(QWizardPage):
    def __init__(self):
        super().__init__()
        self.setTitle("Paths and preferences")
        self.setSubTitle("Browse to your MOVER data and LLM models directory.")
        lay = QVBoxLayout(self)
        self.form = PathsForm(self, compact=True)
        lay.addWidget(scroll_panel(self.form))

    def validatePage(self) -> bool:  # noqa: N802
        try:
            validate_emr_dir(self.form.edit_emr.text().strip())
            self.form.apply_to_runtime(require_emr=True)
        except Exception as e:
            QMessageBox.critical(self, "Setup", str(e))
            return False
        return True


class _DonePage(QWizardPage):
    def __init__(self):
        super().__init__()
        self.setTitle("Ready")
        self.setSubTitle("Setup is complete.")
        lay = QVBoxLayout(self)
        body = QLabel(
            "You can re-open this wizard anytime: press Ctrl+K and choose Setup. "
            "Change individual options in Settings (Ctrl+,)."
        )
        body.setWordWrap(True)
        lay.addWidget(body)
        lay.addStretch(1)


class SetupWizard(QWizard):
    """First-run (or re-run) configuration wizard."""

    setup_finished = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("MOVER Suite — Setup")
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        self.setOption(QWizard.WizardOption.NoBackButtonOnStartPage, True)

        self._welcome = _WelcomePage()
        self._config = _ConfigPage()
        self._done = _DonePage()
        self.addPage(self._welcome)
        self.addPage(self._config)
        self.addPage(self._done)

        self.finished.connect(self._on_finished)
        _size_dialog(self, 680, 620, 640)

    def _on_finished(self, result: int) -> None:
        if result == QDialog.DialogCode.Accepted:
            mark_setup_complete()
            vals = self._config.form.collect()
            self.setup_finished.emit(vals)

    @property
    def form(self) -> PathsForm:
        return self._config.form
