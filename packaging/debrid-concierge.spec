from pathlib import Path


ROOT = Path(SPECPATH).parent
SRC = str(ROOT / "src")

tray = Analysis(
    [str(ROOT / "win_tray.py")],
    pathex=[SRC],
    binaries=[],
    datas=[],
    hiddenimports=["pystray._win32"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
handler = Analysis(
    [str(ROOT / "win_handler.py")],
    pathex=[SRC],
    binaries=[],
    datas=[],
    hiddenimports=["tkinter", "tkinter.filedialog"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
worker = Analysis(
    [str(ROOT / "win_worker.py")],
    pathex=[SRC],
    binaries=[],
    datas=[],
    hiddenimports=["tkinter", "tkinter.filedialog"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

tray_pyz = PYZ(tray.pure)
handler_pyz = PYZ(handler.pure)
worker_pyz = PYZ(worker.pure)

tray_exe = EXE(
    tray_pyz,
    tray.scripts,
    [],
    exclude_binaries=True,
    name="debrid-concierge-tray",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)
handler_exe = EXE(
    handler_pyz,
    handler.scripts,
    [],
    exclude_binaries=True,
    name="debrid-concierge-handler",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)
worker_exe = EXE(
    worker_pyz,
    worker.scripts,
    [],
    exclude_binaries=True,
    name="debrid-concierge-worker",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)

bundle = COLLECT(
    tray_exe,
    tray.binaries,
    tray.datas,
    handler_exe,
    handler.binaries,
    handler.datas,
    worker_exe,
    worker.binaries,
    worker.datas,
    strip=False,
    upx=False,
    name="debrid-concierge",
)
