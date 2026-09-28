"""First-run setup wizard and Preferences / Settings dialog."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
    QWizard,
    QWizardPage,
)

from src.runtime_paths import (
    configure_emr_directory,
    configure_processed_directory,
    configure_wave_directory,
    emr_dir,
    processed_dir,
    wave_dir,
)
from src.guardrails.validate_io import validate_emr_dir
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


def _browse_dir(parent: QWidget, start: str = "") -> str:
    path = QFileDialog.getExistingDirectory(parent, "Select folder", start or str(Path.home()))
    return path or ""


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
        self.combo_default_model = QComboBox()
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
        self.combo_theme = QComboBox()
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
        wrap.addWidget(QLabel(label))
        row = QHBoxLayout()
        row.addWidget(edit, stretch=1)
        btn = QPushButton("Browse…")
        btn.setObjectName("secondaryBtn")
        btn.clicked.connect(browse_slot)
        row.addWidget(btn)
        wrap.addLayout(row)
        return wrap

    def _browse_emr(self) -> None:
        p = _browse_dir(self, self.edit_emr.text())
        if p:
            self.edit_emr.setText(p)

    def _browse_wave(self) -> None:
        p = _browse_dir(self, self.edit_wave.text())
        if p:
            self.edit_wave.setText(p)

    def _browse_processed(self) -> None:
        p = _browse_dir(self, self.edit_processed.text())
        if p:
            self.edit_processed.setText(p)

    def _browse_ollama_models(self) -> None:
        p = _browse_dir(self, self.edit_ollama_models.text())
        if p:
            self.edit_ollama_models.setText(p)

    def load_from_settings(self) -> None:
        cfg = load_settings()
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

        if errors:
            raise ValueError("\n".join(errors))
        return warnings


class SettingsDialog(QDialog):
    """Preferences: reconfigure paths, LLM, and theme anytime."""

    settings_applied = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(560)
        self.resize(620, 560)

        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        self.form = PathsForm(self)
        tabs.addTab(self.form, "General")
        layout.addWidget(tabs)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _save(self) -> None:
        try:
            self.form.apply_to_runtime(require_emr=False)
        except ValueError as e:
            QMessageBox.critical(self, "Settings", str(e))
            return
        mark_setup_complete()
        self.settings_applied.emit(self.form.collect())
        self.accept()


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
            "<p><b>MOVER SIS Monitor</b> is a research desktop app for "
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
        lay.addWidget(body)
        lay.addStretch(1)


class _ConfigPage(QWizardPage):
    def __init__(self):
        super().__init__()
        self.setTitle("Paths and preferences")
        self.setSubTitle("Browse to your MOVER data and LLM models directory.")
        lay = QVBoxLayout(self)
        self.form = PathsForm(self, compact=True)
        lay.addWidget(self.form)

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
        lay.addWidget(
            QLabel(
                "You can re-open this wizard anytime from "
                "File → Setup…, or change individual options under "
                "the ⚙ Settings icon (Ctrl+,)."
            )
        )
        lay.addStretch(1)


class SetupWizard(QWizard):
    """First-run (or re-run) configuration wizard."""

    setup_finished = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("MOVER SIS Monitor — Setup")
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        self.setOption(QWizard.WizardOption.NoBackButtonOnStartPage, True)
        self.setMinimumWidth(640)
        self.resize(680, 620)

        self._welcome = _WelcomePage()
        self._config = _ConfigPage()
        self._done = _DonePage()
        self.addPage(self._welcome)
        self.addPage(self._config)
        self.addPage(self._done)

        self.finished.connect(self._on_finished)

    def _on_finished(self, result: int) -> None:
        if result == QDialog.DialogCode.Accepted:
            mark_setup_complete()
            vals = self._config.form.collect()
            self.setup_finished.emit(vals)

    @property
    def form(self) -> PathsForm:
        return self._config.form
