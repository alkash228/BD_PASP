"""Включает per-monitor DPI до создания окна.

Без этого Windows растягивает картинку целиком, и текст на экране 150% выглядит мыльным.
Вызывать до импорта PyQt.
"""

from __future__ import annotations

import sys


def enable_sharp_dpi() -> None:
    if sys.platform != "win32":
        return
    import ctypes

    try:
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            return
