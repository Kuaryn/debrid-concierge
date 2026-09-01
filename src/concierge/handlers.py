"""CLI entry: concierge <magnet|.torrent> --folder DIR, poll until handed off."""

import argparse
import os
import time

from concierge import orchestrator as orch


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="concierge")
    ap.add_argument("source", help="magnet: link or path to a .torrent file")
    ap.add_argument("--folder", required=True)
    a = ap.parse_args(argv)
    if not a.source.startswith("magnet:") and not os.path.isfile(a.source):
        print("not a magnet and no such file:", a.source)
        return 2
    o = orch.Orchestrator()
    # a re-click of the same magnet must resume, never double-add
    job = next(
        (j for j in o.jobs.values()
         if j.source == a.source and j.state not in (orch.DONE, orch.FAILED)),
        None,
    )
    if job is None:
        # a failed hand-off with a live torrent_id retries; only a failed add re-adds
        job = next(
            (j for j in o.jobs.values()
             if j.source == a.source and j.state == orch.FAILED and j.torrent_id),
            None,
        )
        if job is not None:
            job.state = orch.READY if job.files else orch.CLOUD_PENDING
            job.error = None
    if job is None:
        if a.source.startswith("magnet:"):
            job = o.submit(a.folder, magnet=a.source)
        else:
            job = o.submit(a.folder, torrent_path=a.source)
    while job.state not in (orch.DONE, orch.FAILED):
        time.sleep(o.next_delay(job))
        o.tick()
    print(job.job_id, job.state, job.error or f"handed to abdm into {a.folder}")
    return 0 if job.state == orch.DONE else 1
