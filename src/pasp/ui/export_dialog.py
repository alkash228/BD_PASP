from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from pasp.application.workers import ExportWorker
from pasp.infrastructure.mssql import ConnParams
from pasp.sqlfmt import safe_filename
from pasp.ui.schedule_dialog import remember_folder, remembered_folder


class ExportDialog(QDialog):
    def __init__(self, params: ConnParams, tables: list[tuple[str, str, str]], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Выгрузка таблиц")
        self.setModal(True)
        self.resize(640, 460)
        self.setMinimumWidth(560)
        self._params = params
        self._tables = tables
        self._worker: ExportWorker | None = None
        self._written: dict[str, int] = {}
        self._totals: dict[str, int] = {}
        self._running = False

        self.folder_edit = QLineEdit(remembered_folder())
        self.folder_edit.setPlaceholderText("Папка для .sql")
        browse = QPushButton("Обзор")
        browse.setCursor(Qt.CursorShape.PointingHandCursor)
        browse.clicked.connect(self._browse)
        self.create_box = QCheckBox("Добавить CREATE TABLE, если таблицы ещё нет")
        self.create_box.setChecked(True)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.progress.hide()
        self.status = QLabel(f"Таблиц: {len(tables)}. Существующие файлы с теми же именами будут заменены.")
        self.status.setWordWrap(True)
        self.status.setObjectName("Muted")
        self.log = QPlainTextEdit()
        self.log.setObjectName("MessageLog")
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(300)

        folder_row = QHBoxLayout()
        folder_row.setSpacing(8)
        folder_row.addWidget(self.folder_edit, 1)
        folder_row.addWidget(browse)

        self.start_button = QPushButton("Выгрузить")
        self.start_button.setObjectName("PrimaryButton")
        self.start_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.start_button.clicked.connect(self._start)
        self.close_button = QPushButton("Закрыть")
        self.close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_button.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addStretch(1)
        buttons.addWidget(self.close_button)
        buttons.addWidget(self.start_button)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)
        title = QLabel("Скрипт для перезаливки в SQL Server")
        title.setObjectName("SectionTitle")
        root.addWidget(title)
        root.addWidget(self.status)
        root.addLayout(folder_row)
        root.addWidget(self.create_box)
        root.addWidget(self.progress)
        root.addWidget(self.log, 1)
        root.addLayout(buttons)

    def _browse(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Папка выгрузки", self.folder_edit.text())
        if folder:
            self.folder_edit.setText(folder)
            remember_folder(folder)

    def _start(self) -> None:
        if self._running:
            return
        folder = self.folder_edit.text().strip()
        if not folder:
            self.status.setText("Укажите папку.")
            return
        directory = Path(folder)
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.status.setText(str(exc))
            return
        remember_folder(str(directory))
        jobs = [
            (database, schema, table, directory / safe_filename(f"{database}.{schema}", table))
            for database, schema, table in self._tables
        ]
        self._written.clear()
        self._totals.clear()
        self._running = True
        self.start_button.setEnabled(False)
        self.folder_edit.setEnabled(False)
        self.create_box.setEnabled(False)
        self.progress.setRange(0, 0)
        self.progress.show()
        self.log.clear()
        self.status.setText("Выгрузка…")
        worker = ExportWorker(self._params, jobs, self.create_box.isChecked())
        worker.progress.connect(self._on_progress)
        worker.table_finished.connect(self._on_table_finished)
        worker.table_failed.connect(self._on_table_failed)
        worker.finished_all.connect(self._on_finished)
        worker.finished.connect(worker.deleteLater)
        self._worker = worker
        worker.start()

    def _on_progress(self, key: str, written: int, total: int) -> None:
        self._written[key] = written
        self._totals[key] = total
        done = sum(self._written.values())
        overall = sum(value for value in self._totals.values() if value > 0)
        if overall > 0:
            self.progress.setRange(0, 1000)
            ratio = min(1.0, done / overall)
            self.progress.setValue(int(ratio * 1000))
        target = total if total else "?"
        self.status.setText(f"{key}: {written} / {target}")

    def _on_table_finished(self, key: str, rows: int, path: str) -> None:
        self.log.appendPlainText(f"{key}: {rows} строк → {path}")

    def _on_table_failed(self, key: str, message: str) -> None:
        self.log.appendPlainText(f"{key}: {message}")

    def _on_finished(self, ok: int, bad: int) -> None:
        self._running = False
        self.start_button.setEnabled(True)
        self.folder_edit.setEnabled(True)
        self.create_box.setEnabled(True)
        self.progress.setRange(0, 1000)
        self.progress.setValue(1000 if bad == 0 else self.progress.value())
        self.status.setText(f"Готово. Успешно: {ok}. Ошибок: {bad}.")

    def reject(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.stop()
        super().reject()
