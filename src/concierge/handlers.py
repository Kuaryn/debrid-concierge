"""CLI entry: concierge <magnet|.torrent> --folder DIR [--detach]."""

import argparse
import os
import subprocess
import sys

from concierge import orchestrator as orch
from concierge import worker

# detach flags only exist on windows; tests import this module off-windows
_DETACH_FLAGS = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
    subprocess, "CREATE_NEW_PROCESS_GROUP", 0)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="concierge")
    ap.add_argument("source", help="magnet: link or path to a .torrent file")
    ap.add_argument("--folder",
                    help="download folder; a dialog asks when omitted")
    ap.add_argument("--detach", action="store_true",
                    help="spawn a worker and return at once")
    a = ap.parse_args(argv)
    if not a.source.startswith("magnet:") and not os.path.isfile(a.source):
        print("not a magnet and no such file:", a.source)
        return 2
    if a.detach:
        cmd = [sys.executable, "-m", "concierge.worker", "--source", a.source]
        if a.folder:
            cmd += ["--folder", a.folder]
        subprocess.Popen(
            cmd,
            creationflags=_DETACH_FLAGS,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        print("spawned worker for", a.source)
        return 0
    # blocking mode takes the same lock as a detached worker
    job = worker.run(a.source, a.folder)
    if job is None:
        print("no folder chosen")
        return 0
    print(job.job_id, job.state, job.error or f"handed to abdm into {job.folder}")
    return 0 if job.state == orch.DONE else 1
