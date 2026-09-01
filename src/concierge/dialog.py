"""Folder picker: one dialog, last choice remembered in config."""

import ctypes
import os


def _dpi_aware():
    if os.name != "nt":
        return
    # tkinter is dpi-unaware by default: windows stretches the dialog into
    # a blurry one that also drags oddly
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        ctypes.windll.user32.SetProcessDPIAware()


def ask_folder(initial: str | None = None) -> str | None:
    # imported here so headless CI never needs tkinter
    import tkinter
    from tkinter import filedialog
    _dpi_aware()
    root = tkinter.Tk()
    root.withdraw()
    try:
        return filedialog.askdirectory(
            title="concierge: choose download folder",
            initialdir=initial) or None
    finally:
        root.destroy()
