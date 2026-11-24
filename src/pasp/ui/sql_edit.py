from PyQt6.QtCore import QRect, Qt, QSize
from PyQt6.QtGui import QColor, QPainter, QTextFormat
from PyQt6.QtWidgets import QPlainTextEdit, QTextEdit, QWidget

from pasp.ui.theme import mono_font


class _LineNumberArea(QWidget):
    def __init__(self, editor: "SqlEdit"):
        super().__init__(editor)
        self._editor = editor

    def sizeHint(self) -> QSize:
        return QSize(self._editor.line_number_width(), 0)

    def paintEvent(self, event):
        self._editor.paint_line_numbers(event)


class SqlEdit(QPlainTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("SqlEdit")
        self.setFont(mono_font())
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setTabStopDistance(self.fontMetrics().horizontalAdvance(" ") * 4)
        self.setPlaceholderText("SELECT TOP (100) * FROM dbo.Table;")
        self._gutter = _LineNumberArea(self)
        self.blockCountChanged.connect(self._update_gutter_width)
        self.updateRequest.connect(self._update_gutter)
        self.cursorPositionChanged.connect(self._highlight_current_line)
        self._update_gutter_width()
        self._highlight_current_line()

    def line_number_width(self) -> int:
        digits = max(2, len(str(max(1, self.blockCount()))))
        return 16 + self.fontMetrics().horizontalAdvance("9") * digits

    def _update_gutter_width(self, _count: int = 0) -> None:
        self.setViewportMargins(self.line_number_width(), 0, 0, 0)

    def _update_gutter(self, rect, dy: int) -> None:
        if dy:
            self._gutter.scroll(0, dy)
        else:
            self._gutter.update(0, rect.y(), self._gutter.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_gutter_width()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        bounds = self.contentsRect()
        self._gutter.setGeometry(
            QRect(bounds.left(), bounds.top(), self.line_number_width(), bounds.height())
        )

    def paint_line_numbers(self, event) -> None:
        painter = QPainter(self._gutter)
        painter.fillRect(event.rect(), QColor("#FAFAFA"))
        painter.setPen(QColor("#3B3B3B"))
        painter.setFont(self.font())
        block = self.firstVisibleBlock()
        number = block.blockNumber()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + round(self.blockBoundingRect(block).height())
        height = self.fontMetrics().height()
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                painter.drawText(
                    0,
                    top,
                    self._gutter.width() - 8,
                    height,
                    int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                    str(number + 1),
                )
            block = block.next()
            top = bottom
            bottom = top + round(self.blockBoundingRect(block).height())
            number += 1

    def _highlight_current_line(self) -> None:
        selection = QTextEdit.ExtraSelection()
        selection.format.setBackground(QColor("#E3F0FC"))
        selection.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
        selection.cursor = self.textCursor()
        selection.cursor.clearSelection()
        self.setExtraSelections([selection])
