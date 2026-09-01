"""Folder picker: one dialog, last choice remembered in config."""


def ask_folder(initial: str | None = None) -> str | None:
    # imported here so headless CI never needs tkinter
    import tkinter
    from tkinter import filedialog
    root = tkinter.Tk()
    root.withdraw()
    try:
        return filedialog.askdirectory(
            title="concierge: choose download folder",
            initialdir=initial) or None
    finally:
        root.destroy()
