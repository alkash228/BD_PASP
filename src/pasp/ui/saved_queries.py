from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from pasp.paths import queries_dir
from pasp.ui.sql_edit import SqlEdit

_MAX_BYTES = 2_000_000


def _safe_stem(name: str) -> str:
    cleaned = "".join("_" if char in '<>:"/\\|?*' else char for char in name).strip().strip(".")
    return cleaned or "query"


class SavedQueriesDialog(QDialog):
    run_requested = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Сохранённые запросы")
        self.resize(860, 520)
        self.setMinimumSize(720, 420)
        self._current: Path | None = None

        self.list = QListWidget()
        self.list.setAccessibleName("Список запросов")
        self.list.currentRowChanged.connect(self._open_current)
        self.editor = SqlEdit()
        self.editor.setPlaceholderText("-- текст запроса")

        title = QLabel("Запросы")
        title.setObjectName("SectionTitle")
        hint = QLabel("Файлы лежат в папке пользователя и не зависят от сервера.")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)

        new_button = QPushButton("Новый")
        save_button = QPushButton("Сохранить")
        delete_button = QPushButton("Удалить")
        run_button = QPushButton("Выполнить")
        run_button.setObjectName("PrimaryButton")
        close_button = QPushButton("Закрыть")
        for button in (new_button, save_button, delete_button, run_button, close_button):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
        new_button.clicked.connect(self._new)
        save_button.clicked.connect(self._save)
        delete_button.clicked.connect(self._delete)
        run_button.clicked.connect(self._run)
        close_button.clicked.connect(self.reject)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addWidget(new_button)
        buttons.addWidget(save_button)
        buttons.addWidget(delete_button)
        buttons.addStretch(1)
        buttons.addWidget(close_button)
        buttons.addWidget(run_button)

        editor_col = QVBoxLayout()
        editor_col.setContentsMargins(12, 0, 0, 0)
        editor_col.setSpacing(8)
        editor_col.addWidget(self.editor, 1)
        editor_col.addLayout(buttons)

        side = QVBoxLayout()
        side.setSpacing(8)
        side.addWidget(title)
        side.addWidget(self.list, 1)
        side.addWidget(hint)

        root = QHBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(8)
        root.addLayout(side, 2)
        root.addLayout(editor_col, 5)
        self._reload()

    def _reload(self, select: Path | None = None) -> None:
        folder = queries_dir()
        self.list.clear()
        chosen = -1
        for path in sorted(folder.glob("*.sql"), key=lambda item: item.name.casefold()):
            item = QListWidgetItem(path.stem)
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            self.list.addItem(item)
            if select is not None and path == select:
                chosen = self.list.count() - 1
        if chosen >= 0:
            self.list.setCurrentRow(chosen)
        elif self.list.count() == 0:
            self._current = None

    def _path_of(self, row: int) -> Path | None:
        item = self.list.item(row)
        if item is None:
            return None
        return Path(item.data(Qt.ItemDataRole.UserRole))

    def _open_current(self, row: int) -> None:
        path = self._path_of(row)
        self._current = path
        if path is None or not path.exists():
            return
        if path.stat().st_size > _MAX_BYTES:
            self.editor.setPlainText("")
            QMessageBox.warning(self, "Запрос", "Файл больше 2 МБ и не открыт в редакторе.")
            return
        self.editor.setPlainText(path.read_text(encoding="utf-8"))

    def _new(self) -> None:
        self.list.clearSelection()
        self._current = None
        self.editor.clear()
        self.editor.setFocus(Qt.FocusReason.OtherFocusReason)

    def _save(self) -> None:
        text = self.editor.toPlainText()
        if self._current is None:
            name, ok = QInputDialog.getText(self, "Сохранить запрос", "Имя")
            if not ok:
                return
            stem = _safe_stem(name)
            path = queries_dir() / f"{stem}.sql"
            if path.exists():
                answer = QMessageBox.question(self, "Сохранить запрос", f"Заменить {path.name}?")
                if answer != QMessageBox.StandardButton.Yes:
                    return
        else:
            path = self._current
        path.write_text(text, encoding="utf-8")
        self._current = path
        self._reload(path)

    def _delete(self) -> None:
        if self._current is None or not self._current.exists():
            return
        answer = QMessageBox.question(self, "Удалить запрос", f"Удалить {self._current.name}?")
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._current.unlink(missing_ok=True)
        self.editor.clear()
        self._current = None
        self._reload()

    def _run(self) -> None:
        text = self.editor.toPlainText().strip()
        if not text:
            return
        self.run_requested.emit(self.editor.toPlainText())
        self.accept()
