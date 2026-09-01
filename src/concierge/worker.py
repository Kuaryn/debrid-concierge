"""Detached worker: drive one job to a terminal state, then exit."""

import argparse
import sys
import time

from concierge import orchestrator as orch


def run(source: str, folder: str, o=None) -> int:
    # runs detached with no console; job state lives in jobs.json
    o = o if o is not None else orch.Orchestrator()
    job = o.resume_or_submit(source, folder)
    while job.state not in (orch.DONE, orch.FAILED):
        time.sleep(o.next_delay(job))
        o.tick()
    return 0 if job.state == orch.DONE else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="concierge-worker")
    ap.add_argument("--source", required=True)
    ap.add_argument("--folder", required=True)
    a = ap.parse_args(argv)
    return run(a.source, a.folder)


if __name__ == "__main__":
    sys.exit(main())
