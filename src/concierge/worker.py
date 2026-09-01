"""Detached worker: drain all jobs to terminal states, then exit."""

import argparse
import sys
import time

from concierge import config, dialog, lock
from concierge import orchestrator as orch


def run(source: str, folder: str | None = None, o=None):
    # runs detached with no console; job state lives in jobs.json
    with lock.worker_lock():
        o = o if o is not None else orch.Orchestrator()
        # resumable jobs already know their folder; only a fresh add asks
        if folder is None and o.match(source) is None:
            folder = dialog.ask_folder(config.load().get("last_folder"))
            if folder is None:
                return None
            cfg = config.load()
            cfg["last_folder"] = folder
            config.save(cfg)
        job = o.resume_or_submit(source, folder)
        # the mutex makes this process the only writer, so draining every
        # job here is correct; a second worker waits its turn
        while any(j.state not in orch.TERMINAL for j in o.jobs.values()):
            active = [j for j in o.jobs.values() if j.state not in orch.TERMINAL]
            time.sleep(min(o.next_delay(j) for j in active))
            o.tick()
        return job


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="concierge-worker")
    ap.add_argument("--source", required=True)
    ap.add_argument("--folder")
    a = ap.parse_args(argv)
    job = run(a.source, a.folder)
    return 0 if job is None or job.state == orch.DONE else 1


if __name__ == "__main__":
    sys.exit(main())
