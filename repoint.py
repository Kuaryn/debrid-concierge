"""Cutover: point magnet/.torrent at the concierge, autostart the tray."""

import argparse
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


def main(package_dir: Path | None = None) -> int:
    if package_dir is None:
        handler = f'"{PYW}" "{ROOT / "win_handler.py"}" "%1"'
        tray = f'"{PYW}" "{ROOT / "win_tray.py"}"'
    else:
        pkg = package_dir.resolve()
        # I check the worker too because the handler starts it later
        exes = {
            "handler": pkg / "debrid-concierge-handler.exe",
            "worker": pkg / "debrid-concierge-worker.exe",
            "tray": pkg / "debrid-concierge-tray.exe",
        }
        missing = [p.name for p in exes.values() if not p.is_file()]
        if missing:
            print("missing from package dir:", ", ".join(missing))
            return 1
        handler = f'"{exes["handler"]}" "%1"'
        tray = f'"{exes["tray"]}"'
    _reg_add(r"HKCU\Software\Classes\magnet\shell\open\command", handler)
    # friendly name so the Open-with picker shows a real app name; the
    # UserChoice hash can't be written by hand, the user picks us once
    _reg_add(r"HKCU\Software\Classes\debrid-concierge.torrent", "Debrid Concierge")
    _reg_add(r"HKCU\Software\Classes\debrid-concierge.torrent\shell\open\command", handler)
    _reg_add(r"HKCU\Software\Classes\.torrent", "debrid-concierge.torrent")
    _reg_add(r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run", tray,
             name="debrid-concierge-tray")
    print("repointed magnet + .torrent; tray autostart set")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--package-dir", type=Path, metavar="DIR",
                    help="point at the packaged exes in DIR instead of the dev scripts")
    a = ap.parse_args()
    sys.exit(main(a.package_dir))
