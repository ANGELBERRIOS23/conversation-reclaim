#!/usr/bin/env python3
"""Modern, bilingual Qt desktop interface for Conversation Reclaim."""

import io
import sys
import traceback
from contextlib import redirect_stdout

from PySide6.QtCore import QObject, QRunnable, QSettings, Qt, QThreadPool, Signal
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout,
    QLabel, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar,
    QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

import gui as backend
import reclaim


STRINGS = {
    "es": {
        "eyebrow": "ALMACENAMIENTO DE IA", "title": "Recupera espacio con confianza",
        "subtitle": "Revisa cada categoría. Nada se elimina sin tu confirmación.",
        "language": "Idioma", "recoverable": "recuperables con la selección actual",
        "scan": "Escanear de nuevo", "recommended": "Marcar recomendado",
        "clear": "Desmarcar todo", "badge": "RECOMENDADO",
        "backup_title": "Respaldo opcional",
        "backup_help": "Elige una carpeta externa si quieres una copia completa antes de limpiar.",
        "backup_empty": "Sin respaldo externo", "choose": "Elegir…",
        "close": "Avisar y cerrar OpenCode normalmente si bloquea su base",
        "close_windows": "Confirmo que cerraré OpenCode antes de limpiar su base",
        "activity": "Actividad", "ready": "Listo para revisar",
        "scanning": "Escaneando sin modificar archivos…",
        "scanned": "Escaneo terminado. Revisa la selección.",
        "clean": "Liberar espacio", "nothing_title": "Nada seleccionado",
        "nothing": "Marca al menos una categoría.", "confirm_title": "Confirmar limpieza",
        "confirm_intro": "Se aplicarán estas categorías:",
        "no_backup": "No se creará respaldo externo. Cada cambio quedará en el manifiesto.",
        "with_backup": "Respaldo previo en: {path}", "continue": "¿Quieres continuar?",
        "cleaning": "Aplicando la selección…", "started": "Limpieza iniciada",
        "done": "Limpieza terminada.", "partial": "Limpieza parcial. Revisa la actividad.",
        "result": "Resultado", "freed": "Espacio liberado: {size}",
        "manifest": "Manifiesto: {path}", "backup": "Respaldo: {path}",
        "db_code": "OpenCode terminó con código {code}.",
        "error_title": "No se pudo completar",
        "error": "La operación se detuvo de forma segura. Revisa la actividad.",
        "select_backup": "Destino del respaldo",
        "claude_title": "Claude Code",
        "claude_detail": "Compactaciones y subagentes cerrados. Conserva memoria y contenido reciente.",
        "codex_title": "Codex",
        "codex_detail": "Historial compactado, hijos cerrados y cachés. Protege esta tarea y archivos activos.",
        "opencode_files_title": "OpenCode · temporales",
        "opencode_files_detail": "Snapshots, resultados de herramientas y logs reconstruibles.",
        "opencode_db_title": "OpenCode · conversaciones",
        "opencode_db_detail": "Streaming redundante y mensajes anteriores a la última compactación.",
        "antigravity_title": "Antigravity / Gemini",
        "antigravity_detail": "Compactaciones, scratch, cachés, logs y capturas ya consumidas.",
    },
    "en": {
        "eyebrow": "AI STORAGE", "title": "Reclaim space with confidence",
        "subtitle": "Review every category. Nothing is removed without your confirmation.",
        "language": "Language", "recoverable": "recoverable with the current selection",
        "scan": "Scan again", "recommended": "Select recommended",
        "clear": "Clear selection", "badge": "RECOMMENDED",
        "backup_title": "Optional backup",
        "backup_help": "Choose an external folder for a full copy before cleanup.",
        "backup_empty": "No external backup", "choose": "Choose…",
        "close": "Warn and quit OpenCode normally if it blocks its database",
        "close_windows": "I will close OpenCode before cleaning its database",
        "activity": "Activity", "ready": "Ready to review",
        "scanning": "Scanning without changing files…",
        "scanned": "Scan complete. Review your selection.",
        "clean": "Reclaim space", "nothing_title": "Nothing selected",
        "nothing": "Select at least one category.", "confirm_title": "Confirm cleanup",
        "confirm_intro": "These categories will be applied:",
        "no_backup": "No external backup will be created. Every change remains in the manifest.",
        "with_backup": "Backup destination: {path}", "continue": "Do you want to continue?",
        "cleaning": "Applying your selection…", "started": "Cleanup started",
        "done": "Cleanup complete.", "partial": "Partial cleanup. Review the activity log.",
        "result": "Result", "freed": "Space reclaimed: {size}",
        "manifest": "Manifest: {path}", "backup": "Backup: {path}",
        "db_code": "OpenCode finished with code {code}.",
        "error_title": "Could not complete",
        "error": "The operation stopped safely. Review the activity log.",
        "select_backup": "Backup destination",
        "claude_title": "Claude Code",
        "claude_detail": "Compactions and closed subagents. Keeps memory and recent content.",
        "codex_title": "Codex",
        "codex_detail": "Compacted history, closed children and caches. Protects this task and active files.",
        "opencode_files_title": "OpenCode · temporary files",
        "opencode_files_detail": "Rebuildable snapshots, tool output and logs.",
        "opencode_db_title": "OpenCode · conversations",
        "opencode_db_detail": "Redundant streaming and messages before the latest compaction.",
        "antigravity_title": "Antigravity / Gemini",
        "antigravity_detail": "Compactions, scratch, caches, logs and consumed browser captures.",
    },
}


class Signals(QObject):
    done = Signal(object)
    failed = Signal(str)
    output = Signal(str)


class SignalWriter(io.TextIOBase):
    def __init__(self, signal):
        self.signal = signal

    def write(self, value):
        if value:
            self.signal.emit(value)
        return len(value)


class Worker(QRunnable):
    def __init__(self, function, *args):
        super().__init__()
        self.function, self.args, self.signals = function, args, Signals()

    def run(self):
        try:
            with redirect_stdout(SignalWriter(self.signals.output)):
                result = self.function(*self.args)
            self.signals.done.emit(result)
        except Exception:
            self.signals.failed.emit(traceback.format_exc())


class CategoryCard(QFrame):
    changed = Signal()

    def __init__(self, category, language):
        super().__init__()
        self.category = category
        self.setObjectName("card")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        row = QHBoxLayout(self)
        row.setContentsMargins(18, 15, 18, 15)
        row.setSpacing(14)
        self.check = QCheckBox()
        self.check.setChecked(category["selected"])
        self.check.setEnabled(not bool(category.get("error")))
        self.check.stateChanged.connect(self.changed.emit)
        row.addWidget(self.check, 0, Qt.AlignmentFlag.AlignTop)
        copy = QVBoxLayout()
        copy.setSpacing(5)
        title_row = QHBoxLayout()
        self.title = QLabel()
        self.title.setObjectName("cardTitle")
        self.badge = QLabel()
        self.badge.setObjectName("badge")
        title_row.addWidget(self.title)
        title_row.addWidget(self.badge)
        title_row.addStretch()
        copy.addLayout(title_row)
        self.detail = QLabel()
        self.detail.setObjectName("muted")
        self.detail.setWordWrap(True)
        copy.addWidget(self.detail)
        self.note = QLabel()
        self.note.setObjectName("caption")
        self.note.setWordWrap(True)
        copy.addWidget(self.note)
        row.addLayout(copy, 1)
        self.amount = QLabel(reclaim.human(category["bytes"]))
        self.amount.setObjectName("amount")
        row.addWidget(self.amount, 0, Qt.AlignmentFlag.AlignVCenter)
        self.retranslate(language)

    def retranslate(self, language):
        strings, key = STRINGS[language], self.category["key"]
        self.title.setText(strings[f"{key}_title"])
        self.badge.setText(strings["badge"])
        self.detail.setText(self.category.get("error") or strings[f"{key}_detail"])
        self.note.setText(self.category.get(f"note_{language}", self.category.get("note", "")))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = QSettings("Angel Berrios", "Conversation Reclaim")
        self.language = self.settings.value("language", "es")
        if self.language not in STRINGS:
            self.language = "es"
        self.categories, self.cards = [], {}
        self.pool, self.busy = QThreadPool.globalInstance(), False
        self._build()
        self.retranslate()
        self.scan()

    def t(self, key, **values):
        return STRINGS[self.language][key].format(**values)

    def _build(self):
        self.resize(900, 720)
        self.setMinimumSize(660, 500)
        central = QWidget()
        self.setCentralWidget(central)
        shell = QVBoxLayout(central)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        self.body = QVBoxLayout(content)
        self.body.setContentsMargins(30, 26, 30, 24)
        self.body.setSpacing(16)
        scroll.setWidget(content)
        shell.addWidget(scroll, 1)

        top = QHBoxLayout()
        heading = QVBoxLayout()
        heading.setSpacing(4)
        self.eyebrow, self.title, self.subtitle = QLabel(), QLabel(), QLabel()
        self.eyebrow.setObjectName("eyebrow")
        self.title.setObjectName("title")
        self.subtitle.setObjectName("muted")
        self.subtitle.setWordWrap(True)
        for widget in (self.eyebrow, self.title, self.subtitle):
            heading.addWidget(widget)
        top.addLayout(heading, 1)
        language = QVBoxLayout()
        self.language_label = QLabel()
        self.language_label.setObjectName("caption")
        self.language_combo = QComboBox()
        self.language_combo.addItem("Español", "es")
        self.language_combo.addItem("English", "en")
        self.language_combo.setCurrentIndex(0 if self.language == "es" else 1)
        self.language_combo.currentIndexChanged.connect(self.change_language)
        language.addWidget(self.language_label)
        language.addWidget(self.language_combo)
        top.addLayout(language)
        self.body.addLayout(top)

        summary = QFrame()
        summary.setObjectName("summary")
        summary_row = QHBoxLayout(summary)
        summary_row.setContentsMargins(20, 16, 20, 16)
        self.summary_amount = QLabel("—")
        self.summary_amount.setObjectName("summaryAmount")
        self.summary_text = QLabel()
        self.summary_text.setObjectName("muted")
        self.summary_text.setWordWrap(True)
        self.scan_button = QPushButton()
        self.scan_button.clicked.connect(self.scan)
        summary_row.addWidget(self.summary_amount)
        summary_row.addWidget(self.summary_text, 1)
        summary_row.addWidget(self.scan_button)
        self.body.addWidget(summary)

        controls = QHBoxLayout()
        self.recommended_button, self.clear_button = QPushButton(), QPushButton()
        self.recommended_button.clicked.connect(self.select_recommended)
        self.clear_button.clicked.connect(self.clear_selection)
        controls.addWidget(self.recommended_button)
        controls.addWidget(self.clear_button)
        controls.addStretch()
        self.body.addLayout(controls)
        self.cards_layout = QVBoxLayout()
        self.cards_layout.setSpacing(10)
        self.body.addLayout(self.cards_layout)

        options = QFrame()
        options.setObjectName("card")
        options_layout = QVBoxLayout(options)
        options_layout.setContentsMargins(18, 15, 18, 15)
        options_layout.setSpacing(7)
        self.backup_title, self.backup_help = QLabel(), QLabel()
        self.backup_title.setObjectName("cardTitle")
        self.backup_help.setObjectName("muted")
        self.backup_help.setWordWrap(True)
        options_layout.addWidget(self.backup_title)
        options_layout.addWidget(self.backup_help)
        backup_row = QHBoxLayout()
        self.backup_path = QLabel()
        self.backup_path.setObjectName("pathField")
        self.backup_path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.choose_button = QPushButton()
        self.choose_button.clicked.connect(self.choose_backup)
        backup_row.addWidget(self.backup_path, 1)
        backup_row.addWidget(self.choose_button)
        options_layout.addLayout(backup_row)
        self.close_opencode = QCheckBox()
        self.close_opencode.setChecked(True)
        options_layout.addWidget(self.close_opencode)
        self.body.addWidget(options)

        self.activity_title = QLabel()
        self.activity_title.setObjectName("sectionTitle")
        self.body.addWidget(self.activity_title)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(1500)
        self.log.setMinimumHeight(100)
        self.log.setMaximumHeight(160)
        self.body.addWidget(self.log)

        footer = QFrame()
        footer.setObjectName("footer")
        footer_row = QHBoxLayout(footer)
        footer_row.setContentsMargins(26, 13, 26, 13)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setFixedWidth(100)
        self.progress.hide()
        self.status = QLabel()
        self.status.setObjectName("muted")
        self.clean_button = QPushButton()
        self.clean_button.setObjectName("primary")
        self.clean_button.clicked.connect(self.confirm_cleanup)
        footer_row.addWidget(self.progress)
        footer_row.addWidget(self.status, 1)
        footer_row.addWidget(self.clean_button)
        shell.addWidget(footer)

    def change_language(self):
        self.language = self.language_combo.currentData()
        self.settings.setValue("language", self.language)
        self.retranslate()

    def retranslate(self):
        self.setWindowTitle("Conversation Reclaim")
        pairs = ((self.eyebrow, "eyebrow"), (self.title, "title"),
                 (self.subtitle, "subtitle"), (self.language_label, "language"),
                 (self.summary_text, "recoverable"), (self.scan_button, "scan"),
                 (self.recommended_button, "recommended"), (self.clear_button, "clear"),
                 (self.backup_title, "backup_title"), (self.backup_help, "backup_help"),
                 (self.choose_button, "choose"),
                 (self.activity_title, "activity"), (self.clean_button, "clean"))
        for widget, key in pairs:
            widget.setText(self.t(key))
        self.close_opencode.setText(self.t("close_windows" if sys.platform == "win32" else "close"))
        if not self.backup_path.property("chosenPath"):
            self.backup_path.setText(self.t("backup_empty"))
        if not self.busy:
            self.status.setText(self.t("ready"))
        for card in self.cards.values():
            card.retranslate(self.language)

    def render_cards(self):
        while self.cards_layout.count():
            item = self.cards_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.cards = {}
        for category in self.categories:
            card = CategoryCard(category, self.language)
            card.changed.connect(self.update_summary)
            self.cards[category["key"]] = card
            self.cards_layout.addWidget(card)
        self.update_summary()

    def selected_keys(self):
        return [key for key in backend.CATEGORY_ORDER
                if key in self.cards and self.cards[key].check.isChecked()]

    def update_summary(self):
        chosen = set(self.selected_keys())
        total = sum(item["bytes"] for item in self.categories if item["key"] in chosen)
        self.summary_amount.setText(reclaim.human(total))

    def select_recommended(self):
        for item in self.categories:
            self.cards[item["key"]].check.setChecked(
                bool(item["recommended"] and item["bytes"] > 0 and not item.get("error")))

    def clear_selection(self):
        for card in self.cards.values():
            card.check.setChecked(False)

    def choose_backup(self):
        path = QFileDialog.getExistingDirectory(self, self.t("select_backup"))
        if path:
            self.backup_path.setProperty("chosenPath", path)
            self.backup_path.setText(path)

    def set_busy(self, busy, message):
        self.busy = busy
        self.status.setText(message)
        self.progress.setVisible(busy)
        for widget in (self.scan_button, self.clean_button, self.recommended_button,
                       self.clear_button, self.choose_button):
            widget.setEnabled(not busy)

    def start_worker(self, function, completed, *args):
        worker = Worker(function, *args)
        worker.signals.output.connect(self.append_log)
        worker.signals.done.connect(completed)
        worker.signals.failed.connect(self.operation_failed)
        self.pool.start(worker)

    def scan(self):
        if self.busy:
            return
        self.set_busy(True, self.t("scanning"))
        self.start_worker(backend.scan_categories, self.scan_finished)

    def scan_finished(self, categories):
        self.categories = categories
        self.render_cards()
        self.set_busy(False, self.t("scanned"))

    def confirm_cleanup(self):
        keys = self.selected_keys()
        if not keys:
            QMessageBox.information(self, self.t("nothing_title"), self.t("nothing"))
            return
        chosen = [item for item in self.categories if item["key"] in keys]
        lines = [f"• {self.t(item['key'] + '_title')}: {reclaim.human(item['bytes'])}"
                 for item in chosen]
        backup = self.backup_path.property("chosenPath") or ""
        backup_text = self.t("with_backup", path=backup) if backup else self.t("no_backup")
        prompt = (self.t("confirm_intro") + "\n\n" + "\n".join(lines) + "\n\n" +
                  backup_text + "\n\n" + self.t("continue"))
        if QMessageBox.question(self, self.t("confirm_title"), prompt) != QMessageBox.StandardButton.Yes:
            return
        self.append_log(f"\n=== {self.t('started')} ===\n")
        self.set_busy(True, self.t("cleaning"))
        self.start_worker(backend.run_cleanup, self.cleanup_finished, keys,
                          backup or None, self.close_opencode.isChecked())

    def cleanup_finished(self, result):
        self.set_busy(False, self.t("done") if result["success"] else self.t("partial"))
        lines = [self.t("freed", size=reclaim.human(result["freed"]))]
        for key in ("manifest", "backup"):
            if result.get(key):
                lines.append(self.t(key, path=result[key]))
        if result.get("db_code") not in (None, 0):
            lines.append(self.t("db_code", code=result["db_code"]))
        QMessageBox.information(self, self.t("result"), "\n".join(lines))
        self.scan()

    def operation_failed(self, details):
        self.append_log(details)
        self.set_busy(False, self.t("error"))
        QMessageBox.critical(self, self.t("error_title"), self.t("error"))

    def append_log(self, value):
        cursor = self.log.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.log.setTextCursor(cursor)
        self.log.insertPlainText(value)
        self.log.ensureCursorVisible()


STYLE = """
QWidget { font-size: 14px; }
QMainWindow, QScrollArea, QScrollArea > QWidget > QWidget { background: palette(window); }
QLabel#eyebrow { color: #3973d6; font-size: 11px; font-weight: 700; }
QLabel#title { font-size: 29px; font-weight: 700; }
QLabel#sectionTitle, QLabel#cardTitle { font-size: 15px; font-weight: 650; }
QLabel#muted { color: #667085; }
QLabel#caption { color: #8a94a3; font-size: 12px; }
QLabel#amount { font-size: 17px; font-weight: 700; }
QLabel#summaryAmount { font-size: 25px; font-weight: 750; color: #175cd3; }
QLabel#badge { color: #067647; background: #ecfdf3; border-radius: 7px; padding: 3px 7px; font-size: 10px; font-weight: 700; }
QLabel#pathField { border: 1px solid #d0d5dd; border-radius: 8px; padding: 9px 11px; color: #667085; }
QFrame#card, QFrame#summary { background: palette(base); border: 1px solid #dfe3e8; border-radius: 12px; }
QFrame#footer { background: palette(base); border-top: 1px solid #dfe3e8; }
QPushButton { min-height: 28px; padding: 4px 13px; }
QPushButton#primary { color: white; background: #1769e0; border: 0; border-radius: 9px; min-height: 38px; padding: 2px 20px; font-weight: 650; }
QPushButton#primary:hover { background: #1258bd; }
QPlainTextEdit { background: palette(base); border: 1px solid #dfe3e8; border-radius: 10px; padding: 8px; font-family: Menlo, Consolas, monospace; font-size: 12px; }
QProgressBar { border: 0; background: #e4e7ec; border-radius: 3px; max-height: 6px; }
QProgressBar::chunk { background: #1769e0; border-radius: 3px; }
"""


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["--smoke-test"]:
        return 0 if len(backend.scan_categories()) == len(backend.CATEGORY_ORDER) else 2
    if args:
        print("Usage: desktop.py [--smoke-test]")
        return 2
    app = QApplication(sys.argv[:1])
    app.setApplicationName("Conversation Reclaim")
    app.setOrganizationName("Angel Berrios")
    app.setStyleSheet(STYLE)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
