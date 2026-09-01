"""Tray icon: poll jobs.json, tooltip with active count, toasts on done/fail."""

import json
import threading
import time

from concierge import lock
from concierge import orchestrator as orch

POLL = 2.0
TERMINAL = (orch.DONE, orch.FAILED)


def load_jobs() -> list[dict]:
    try:
        raw = json.loads(orch.JOBS_FILE.read_text())
    except (OSError, ValueError):
        return []
    return [j for j in raw if isinstance(j, dict)]


def tooltip(jobs: list[dict]) -> str:
    active = [j for j in jobs if j.get("state") not in TERMINAL]
    return f"concierge: {len(active)} active" if active else "concierge: idle"


def toast_events(prev: dict, jobs: list[dict]) -> list[tuple[str, str]]:
    events = []
    for j in jobs:
        old = prev.get(j.get("job_id"))
        new = j.get("state")
        # first sight is a baseline: no toast for states reached before the tray
        if old is None or old == new or new not in TERMINAL:
            continue
        if new == orch.DONE:
            events.append(("concierge", f"{j.get('job_id')} handed to abdm"))
        else:
            err = (j.get("error") or "unknown error")[:120]
            events.append(("concierge failed", f"{j.get('job_id')}: {err}"))
    return events


def run() -> None:
    if lock.already_running("debrid-concierge-tray"):
        return  # second instance would double every toast
    # pystray and pillow are windows-side deps; keep them out of CI imports
    import pystray
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (64, 64))
    d = ImageDraw.Draw(img)
    # drawn in code so the repo carries no binary assets
    d.rounded_rectangle((4, 4, 60, 60), radius=14, fill=(30, 120, 200, 255))
    d.ellipse((22, 22, 42, 42), fill=(255, 255, 255, 255))

    icon = pystray.Icon("concierge", img, tooltip(load_jobs()))
    icon.menu = pystray.Menu(pystray.MenuItem("Exit", lambda i, _: i.stop()))
    seen = {j.get("job_id"): j.get("state") for j in load_jobs()}

    def poll():
        while True:
            time.sleep(POLL)
            jobs = load_jobs()
            icon.title = tooltip(jobs)
            for title, msg in toast_events(seen, jobs):
                icon.notify(msg, title)
            seen.clear()
            seen.update({j.get("job_id"): j.get("state") for j in jobs})

    threading.Thread(target=poll, daemon=True).start()
    icon.run()


if __name__ == "__main__":
    run()
