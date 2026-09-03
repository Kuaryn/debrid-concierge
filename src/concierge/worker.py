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


def _state_failure(source, folder, error):
    message = str(error)
    dialog.show_error(message)
    return orch.Job(source=source, folder=folder or "", state=orch.FAILED, error=message)


def run(source: str, folder: str | None = None, o=None):
    picked = False
    try:
        with lock.worker_lock():
            if o is None:
                o = orch.Orchestrator()
            else:
                o.reload()
            warning = getattr(o, "load_warning", None)
            current = o.match(source) if folder is None else None
            initial = config.load().get("last_folder") if folder is None and current is None else None
    except (config.ConfigError, orch.JobsError) as e:
        return _state_failure(source, folder, e)
    if warning:
        dialog.show_error(warning)
    if folder is None and current is None:
        folder = dialog.ask_folder(initial)
        if folder is None:
            return None
        picked = True
    try:
        with lock.worker_lock():
            warning = o.reload()
            if picked:
                cfg = config.load()
                cfg["last_folder"] = folder
                config.save(cfg)
            job = o.resume_or_submit(source, folder)
            job_id = job.job_id
    except (config.ConfigError, orch.JobsError) as e:
        return _state_failure(source, folder, e)
    if warning:
        dialog.show_error(warning)
    while True:
        try:
            with lock.worker_lock():
                warning = o.reload()
                active = [j for j in o.jobs.values() if j.state not in orch.TERMINAL]
                if active:
                    o.tick(now=time.time())
                    active = [j for j in o.jobs.values() if j.state not in orch.TERMINAL]
                result = None if active else o.jobs.get(job_id, job)
                wait = _next_wait(active, time.time()) if active else None
        except orch.JobsError as e:
            return _state_failure(source, folder, e)
        if warning:
            dialog.show_error(warning)
        if result is not None:
            return result
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
