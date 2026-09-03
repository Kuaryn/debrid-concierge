"""Detached worker: drain all jobs to terminal states, then exit."""

import argparse
import sys
import time

from concierge import config, dialog, lock
from concierge import orchestrator as orch


def _next_wait(jobs, now):
    waits = [max(0, j.next_poll_at - now) if j.state == orch.CLOUD_PENDING else 0
             for j in jobs]
    return min(waits)


def run(source: str, folder: str | None = None, o=None):
    picked = False
    with lock.worker_lock():
        o = o if o is not None else orch.Orchestrator()
        o.reload()
        current = o.match(source) if folder is None else None
        initial = config.load().get("last_folder") if folder is None and current is None else None
    if folder is None and current is None:
        folder = dialog.ask_folder(initial)
        if folder is None:
            return None
        picked = True
    with lock.worker_lock():
        o.reload()
        if picked:
            cfg = config.load()
            cfg["last_folder"] = folder
            config.save(cfg)
        job = o.resume_or_submit(source, folder)
        job_id = job.job_id
    while True:
        with lock.worker_lock():
            o.reload()
            active = [j for j in o.jobs.values() if j.state not in orch.TERMINAL]
            if not active:
                return o.jobs.get(job_id, job)
            o.tick(now=time.time())
            active = [j for j in o.jobs.values() if j.state not in orch.TERMINAL]
            if not active:
                return o.jobs.get(job_id, job)
            wait = _next_wait(active, time.time())
        time.sleep(wait)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="concierge-worker")
    ap.add_argument("--source", required=True)
    ap.add_argument("--folder")
    a = ap.parse_args(argv)
    job = run(a.source, a.folder)
    return 0 if job is None or job.state == orch.DONE else 1


if __name__ == "__main__":
    sys.exit(main())
