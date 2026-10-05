"""OScope entry point.  Run with:  python main.py"""

from __future__ import annotations

import os
import sys


def _fail(message: str) -> int:
    """Show a plain-language message (dialog if possible, console otherwise). No tracebacks."""
    print(message, file=sys.stderr)
    try:
        import tkinter
        from tkinter import messagebox

        root = tkinter.Tk()
        root.withdraw()
        messagebox.showerror("OScope", message)
        root.destroy()
    except Exception:
        pass
    return 1


def main() -> int:
    from app.core import platform as oscope_platform

    if not oscope_platform.is_supported():
        return _fail(oscope_platform.UNSUPPORTED_MESSAGE)

    try:
        import psutil  # noqa: F401
    except ImportError:
        return _fail("The 'psutil' package is not installed.\nRun:  pip install -r requirements.txt")

    try:
        import tkinter  # noqa: F401
    except ImportError:
        return _fail("Tkinter is not available in this Python installation.\nInstall Python 3.11+ from python.org.")

    try:
        from app.gui.main_window import run
        from app.utils.logging_setup import setup_logging

        setup_logging()
        run()
    except Exception as exc:  # noqa: BLE001
        if os.environ.get("OSCOPE_DEBUG") == "1":
            raise
        return _fail(f"OScope could not start ({type(exc).__name__}). Set OSCOPE_DEBUG=1 for details.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
