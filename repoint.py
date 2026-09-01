"""Cutover: point magnet/.torrent at the concierge, autostart the tray."""

import subprocess
import sys
from pathlib import Path

PYW = sys.executable.replace("python.exe", "pythonw.exe")
ROOT = Path(__file__).parent


def _reg_add(key: str, value: str, name: str | None = None) -> None:
    args = ["reg", "add", key]
    args += ["/v", name] if name else ["/ve"]
    args += ["/t", "REG_SZ", "/d", value, "/f"]
    subprocess.run(args, check=True)


def main() -> int:
    handler = f'"{PYW}" "{ROOT / "win_handler.py"}" "%1"'
    _reg_add(r"HKCU\Software\Classes\magnet\shell\open\command", handler)
    # friendly name so the Open-with picker shows a real app name; the
    # UserChoice hash can't be written by hand, the user picks us once
    _reg_add(r"HKCU\Software\Classes\debrid-concierge.torrent", "Debrid Concierge")
    _reg_add(r"HKCU\Software\Classes\debrid-concierge.torrent\shell\open\command", handler)
    _reg_add(r"HKCU\Software\Classes\.torrent", "debrid-concierge.torrent")
    _reg_add(r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run",
             f'"{PYW}" "{ROOT / "win_tray.py"}"', name="debrid-concierge-tray")
    print("repointed magnet + .torrent; tray autostart set")
    return 0


if __name__ == "__main__":
    sys.exit(main())
