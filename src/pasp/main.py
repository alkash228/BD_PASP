import sys

from pasp.dpi import enable_sharp_dpi

enable_sharp_dpi()

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from pasp.ui.main_window import MainWindow
from pasp.ui.theme import apply_theme


def main() -> int:
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setApplicationName("BD PASP")
    app.setOrganizationName("PASP")
    app.setQuitOnLastWindowClosed(False)
    apply_theme(app)
    window = MainWindow()
    window.show()
    window.raise_()
    window.activateWindow()
    return app.exec()
