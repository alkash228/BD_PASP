from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
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

from pasp.application.workers import ScriptWorker
from pasp.infrastructure.mssql import ConnParams
from pasp.sqlfmt import script_target


class LoadDialog(QDialog):
    """Вставляет строки из файла выгрузки в таблицу на текущем сервере."""

    def __init__(self, params: ConnParams, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Загрузить файл на сервер")
        self.setModal(True)
        self.resize(640, 420)
        self.setMinimumWidth(560)
        self._params = params
        self._worker: ScriptWorker | None = None
        self._running = False

        self.file_edit = QLineEdit()
        self.file_edit.setPlaceholderText("Файл выгрузки таблицы, .sql")
        browse = QPushButton("Обзор")
        browse.setCursor(Qt.CursorShape.PointingHandCursor)
        browse.clicked.connect(self._browse)
        self.schema_edit = QLineEdit("dbo")
        self.table_edit = QLineEdit()
        self.table_edit.setPlaceholderText("Таблица на этом сервере")
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.hide()
        self.status = QLabel(
            f"Берутся строки из файла выгрузки и вставляются в таблицу на {params.server}, "
            f"база «{params.database or 'по умолчанию'}». Таблица на сервере уже должна существовать."
        )
        self.status.setWordWrap(True)
        self.status.setObjectName("Muted")
        self.log = QPlainTextEdit()
        self.log.setObjectName("MessageLog")
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(200)

        file_row = QHBoxLayout()
        file_row.setSpacing(8)
        file_row.addWidget(self.file_edit, 1)
        file_row.addWidget(browse)

        names = QHBoxLayout()
        names.setSpacing(8)
        names.addWidget(QLabel("Схема"))
        names.addWidget(self.schema_edit, 1)
        names.addWidget(QLabel("Таблица на моём сервере"))
        names.addWidget(self.table_edit, 2)

        self.start_button = QPushButton("Загрузить в таблицу")
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
        title = QLabel("Загрузить выгруженную таблицу на этот сервер")
        title.setObjectName("SectionTitle")
        root.addWidget(title)
        root.addWidget(self.status)
        root.addLayout(file_row)
        root.addLayout(names)
        root.addWidget(self.progress)
        root.addWidget(self.log, 1)
        root.addLayout(buttons)

    def _browse(self) -> None:
        path, _selected = QFileDialog.getOpenFileName(self, "Файл SQL", self.file_edit.text(), "SQL (*.sql);;Все файлы (*.*)")
        if path:
            self.file_edit.setText(path)
            self._fill_target(Path(path))

    def _fill_target(self, path: Path) -> None:
        try:
            head = path.read_text(encoding="utf-8-sig")[:200_000]
        except OSError:
            return
        found = script_target(head)
        if found is None:
            return
        schema, table = found
        self.schema_edit.setText(schema)
        self.table_edit.setText(table)

    def _start(self) -> None:
        if self._running:
            return
        raw = self.file_edit.text().strip()
        path = Path(raw)
        if not raw or not path.is_file():
            self.status.setText("Выберите существующий файл .sql.")
            return
        schema = self.schema_edit.text().strip()
        table = self.table_edit.text().strip()
        if not schema or not table:
            self.status.setText("Укажите схему и таблицу на этом сервере, куда вставлять строки.")
            return
        self._running = True
        self.start_button.setEnabled(False)
        self.file_edit.setEnabled(False)
        self.schema_edit.setEnabled(False)
        self.table_edit.setEnabled(False)
        self.progress.setRange(0, 0)
        self.progress.show()
        self.log.clear()
        self.status.setText(f"Загрузка в [{schema}].[{table}]…")
        worker = ScriptWorker(self._params, path, schema, table)
        worker.progress.connect(self._on_progress)
        worker.message.connect(self.log.appendPlainText)
        worker.succeeded.connect(self._on_ok)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(worker.deleteLater)
        self._worker = worker
        worker.start()

    def _on_progress(self, index: int, total: int) -> None:
        self.progress.setRange(0, total)
        self.progress.setValue(index)
        self.status.setText(f"Команда {index} из {total}")

    def _on_ok(self, batches: int) -> None:
        self._running = False
        self._unlock()
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self.status.setText(f"Готово. Выполнено команд: {batches}.")
        self.log.appendPlainText(self.status.text())

    def _on_failed(self, message: str) -> None:
        self._running = False
        self._unlock()
        self.progress.hide()
        self.status.setText(message)
        self.log.appendPlainText(message)

    def _unlock(self) -> None:
        self.start_button.setEnabled(True)
        self.file_edit.setEnabled(True)
        self.schema_edit.setEnabled(True)
        self.table_edit.setEnabled(True)

    def reject(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.stop()
        super().reject()
