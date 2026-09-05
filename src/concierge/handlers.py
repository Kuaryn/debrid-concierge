"""CLI entry: concierge <magnet|.torrent> --folder DIR [--detach]."""

import argparse
import os
import subprocess
import sys
from pathlib import Path

from concierge import orchestrator as orch
from concierge import worker

# detach flags only exist on windows; tests import this module off-windows
_DETACH_FLAGS = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
    subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
# path-based entry: registry spawns run pythonw with no PYTHONPATH
WORKER_ENTRY = Path(__file__).resolve().parents[2] / "win_worker.py"
WORKER_EXE = "debrid-concierge-worker.exe"


def _worker_command(source: str) -> list[str]:
    if getattr(sys, "frozen", False):
        return [str(Path(sys.executable).with_name(WORKER_EXE)), "--source", source]
    return [sys.executable, str(WORKER_ENTRY), "--source", source]


def _source(value: str) -> str:
    if value.startswith("-") or '"' in value or any(
        ord(c) < 32 or 127 <= ord(c) <= 159 for c in value
    ):
        raise argparse.ArgumentTypeError("invalid source")
    return value


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="concierge")
    ap.add_argument("source", type=_source, help="magnet: link or path to a .torrent file")
    ap.add_argument("--folder",
                    help="download folder; a dialog asks when omitted")
    ap.add_argument("--detach", action="store_true",
                    help="spawn a worker and return at once")
    a = ap.parse_args(argv)
    if not a.source.startswith("magnet:") and not os.path.isfile(a.source):
        print("not a magnet and no such file:", a.source)
        return 2
    if a.detach:
        cmd = _worker_command(a.source)
        if a.folder:
            cmd += ["--folder", a.folder]
        env = None
        if getattr(sys, "frozen", False):
            env = os.environ.copy()
            # I reset the bootloader state because this worker outlives its handler
            env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
        subprocess.Popen(
            cmd,
            creationflags=_DETACH_FLAGS,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env=env,
        )
        return 0
    # blocking mode takes the same lock as a detached worker
    job = worker.run(a.source, a.folder)
    if job is None:
        print("no folder chosen")
        return 0
    print(job.job_id, job.state, job.error or f"handed to abdm into {job.folder}")
    return 0 if job.state == orch.DONE else 1
