"""Светлая тема по Fluent 2: нейтральный холст и один синий акцент.

Токены: фон #F5F5F5, текст #242424, акцент #0F6CBD, сетка 4 px, Segoe UI 14 px.
Контраст основного текста к белому выше 4.5:1.
"""

from PyQt6.QtGui import QColor, QFont, QPalette
from PyQt6.QtWidgets import QApplication

STYLESHEET = """
QMainWindow, QDialog {
    background: #F5F5F5;
    color: #242424;
}
QFrame#AccentBar {
    background: #0F6CBD;
    border: none;
}
QFrame#Card {
    background: #FFFFFF;
    border: 1px solid #C4C4C4;
    border-radius: 6px;
}
QLabel {
    background: transparent;
    color: #242424;
}
QLabel#SectionTitle, QLabel#PaneTitle {
    color: #242424;
    font-weight: 600;
}
QLabel#PaneTitle {
    background: #F7F7F7;
    border-bottom: 1px solid #C4C4C4;
    padding: 6px 12px;
}
QLabel#EmptyHint, QLabel#Muted {
    color: #3B3B3B;
}
QLabel#ErrorText {
    color: #C50F1F;
}
QLineEdit, QComboBox, QPlainTextEdit#MessageLog {
    background: #FFFFFF;
    color: #242424;
    border: 1px solid #BDBDBD;
    border-radius: 4px;
    padding: 4px 8px;
    selection-background-color: #0F6CBD;
    selection-color: #FFFFFF;
}
QLineEdit:hover, QComboBox:hover {
    border-color: #B3B3B3;
}
QLineEdit:focus, QComboBox:focus, QPlainTextEdit#SqlEdit:focus {
    border: 1px solid #0F6CBD;
}
QLineEdit:disabled, QComboBox:disabled, QPlainTextEdit:disabled {
    color: #616161;
    background: #F5F5F5;
}
QComboBox::drop-down {
    border: none;
    width: 24px;
}
QComboBox QAbstractItemView {
    background: #FFFFFF;
    color: #242424;
    border: 1px solid #BDBDBD;
    selection-background-color: #EBF3FC;
    selection-color: #242424;
    outline: 0;
}
QPlainTextEdit#SqlEdit {
    background: #FFFFFF;
    color: #242424;
    border: none;
    selection-background-color: #0F6CBD;
    selection-color: #FFFFFF;
}
QPlainTextEdit#MessageLog {
    background: #FAFAFA;
}
QPushButton {
    background: #FFFFFF;
    color: #242424;
    border: 1px solid #BDBDBD;
    border-radius: 4px;
    padding: 4px 12px;
    min-height: 32px;
}
QPushButton:hover {
    background: #F5F5F5;
    border-color: #B3B3B3;
}
QPushButton:pressed {
    background: #E6E6E6;
}
QPushButton:disabled {
    color: #8A8A8A;
    background: #F5F5F5;
    border-color: #E0E0E0;
}
QPushButton#PrimaryButton {
    background: #0F6CBD;
    color: #FFFFFF;
    border: 1px solid #0F6CBD;
}
QPushButton#PrimaryButton:hover {
    background: #115EA3;
    border-color: #115EA3;
}
QPushButton#PrimaryButton:pressed {
    background: #0F548C;
}
QPushButton#PrimaryButton:disabled {
    background: #C8C8C8;
    color: #FFFFFF;
    border-color: #C8C8C8;
}
QTreeWidget, QListWidget {
    background: #FFFFFF;
    color: #242424;
    border: none;
    outline: 0;
}
QTreeWidget::item, QListWidget::item {
    min-height: 28px;
    padding: 2px 4px;
}
QTreeWidget::item:hover, QListWidget::item:hover {
    background: #F5F5F5;
}
QTreeWidget::item:selected, QListWidget::item:selected {
    background: #EBF3FC;
    color: #242424;
}
QTableView {
    background: #FFFFFF;
    color: #242424;
    alternate-background-color: #FAFAFA;
    gridline-color: #EDEBE9;
    border: none;
    selection-background-color: #EBF3FC;
    selection-color: #242424;
}
QHeaderView::section {
    background: #FAFAFA;
    color: #424242;
    border: none;
    border-bottom: 1px solid #BDBDBD;
    border-right: 1px solid #EDEBE9;
    padding: 6px 8px;
    font-weight: 600;
}
QCheckBox {
    spacing: 8px;
    min-height: 28px;
}
QCheckBox::indicator {
    width: 16px;
    height: 16px;
}
QSplitter::handle {
    background: #E6E6E6;
}
QSplitter::handle:horizontal {
    width: 6px;
}
QSplitter::handle:vertical {
    height: 6px;
}
QStatusBar {
    background: #FFFFFF;
    color: #242424;
    border-top: 1px solid #BDBDBD;
}
QStatusBar::item {
    border: none;
}
QProgressBar {
    background: #E6E6E6;
    border: none;
    border-radius: 2px;
    min-height: 4px;
    max-height: 4px;
}
QProgressBar::chunk {
    background: #0F6CBD;
    border-radius: 2px;
}
QScrollBar:vertical {
    background: transparent;
    width: 10px;
    margin: 0;
}
QScrollBar:horizontal {
    background: transparent;
    height: 10px;
    margin: 0;
}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {
    background: #C4C4C4;
    border-radius: 4px;
    min-height: 24px;
    min-width: 24px;
}
QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {
    background: #8A8A8A;
}
QScrollBar::add-line, QScrollBar::sub-line,
QScrollBar::add-page, QScrollBar::sub-page {
    background: transparent;
    height: 0;
    width: 0;
}
QToolTip {
    background: #FFFFFF;
    color: #242424;
    border: 1px solid #BDBDBD;
    padding: 4px 8px;
}
"""


def _sharp(font: QFont) -> QFont:
    font.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    return font


def body_font() -> QFont:
    font = QFont("Segoe UI")
    font.setPointSize(10)
    font.setStyleHint(QFont.StyleHint.SansSerif)
    return _sharp(font)


def mono_font() -> QFont:
    font = QFont()
    font.setFamilies(["Cascadia Mono", "Cascadia Code", "Consolas", "Courier New"])
    font.setPointSize(10)
    font.setStyleHint(QFont.StyleHint.Monospace)
    return _sharp(font)


def apply_theme(app: QApplication) -> None:
    app.setStyle("Fusion")
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#F5F5F5"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#242424"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#FFFFFF"))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#FAFAFA"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#242424"))
    palette.setColor(QPalette.ColorRole.Button, QColor("#FFFFFF"))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#242424"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#0F6CBD"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#FFFFFF"))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor("#616161"))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor("#FFFFFF"))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor("#242424"))
    app.setPalette(palette)
    app.setFont(body_font())
    app.setStyleSheet(STYLESHEET)
