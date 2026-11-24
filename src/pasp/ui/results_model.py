from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QStyle, QStyledItemDelegate

_TEXT = QColor("#242424")
_ALT = QColor("#FAFAFA")
_SELECTED = QColor("#EBF3FC")
_WHITE = QColor("#FFFFFF")


class ResultModel(QAbstractTableModel):
    """Строки уже отформатированы в рабочем потоке. data() только читает ячейку."""

    def __init__(self):
        super().__init__()
        self._columns: list[str] = []
        self._rows: list[list[str]] = []

    def clear(self) -> None:
        self.beginResetModel()
        self._columns = []
        self._rows = []
        self.endResetModel()

    def set_columns(self, columns: list[str]) -> None:
        self.beginResetModel()
        self._columns = list(columns)
        self._rows = []
        self.endResetModel()

    def add_rows(self, rows: list[list[str]]) -> None:
        if not rows:
            return
        start = len(self._rows)
        self.beginInsertRows(QModelIndex(), start, start + len(rows) - 1)
        self._rows.extend(rows)
        self.endInsertRows()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._columns)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or role != Qt.ItemDataRole.DisplayRole:
            return None
        row = index.row()
        column = index.column()
        if row < 0 or column < 0 or row >= len(self._rows) or column >= len(self._rows[row]):
            return None
        return self._rows[row][column]

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal:
            if 0 <= section < len(self._columns):
                return self._columns[section]
            return None
        return str(section + 1)


class FastCellDelegate(QStyledItemDelegate):
    """Рисует текст сам, без стиля каждой ячейки. Иначе прокрутка перерисовывает таблицу через QSS."""

    def paint(self, painter: QPainter, option, index: QModelIndex) -> None:
        painter.save()
        painter.setClipRect(option.rect)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        if selected:
            painter.fillRect(option.rect, _SELECTED)
        elif index.row() % 2:
            painter.fillRect(option.rect, _ALT)
        else:
            painter.fillRect(option.rect, _WHITE)
        raw = index.data(Qt.ItemDataRole.DisplayRole)
        if raw:
            text = str(raw)
            rect = option.rect.adjusted(8, 0, -6, 0)
            width = max(0, rect.width())
            average = max(1, option.fontMetrics.averageCharWidth())
            visible = width // average + 2
            if len(text) > visible:
                text = text[:visible]
            painter.setPen(_TEXT)
            painter.setFont(option.font)
            painter.drawText(
                rect,
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                text,
            )
        painter.restore()
