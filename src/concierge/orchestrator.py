"""Job state machine: cloud add, poll to completion, per-file hand-off to ABDM."""

import json
import os
import uuid
from dataclasses import asdict, dataclass, field

from concierge import abdm, config
from concierge.providers import torbox

JOBS_FILE = config.APP_DIR / "jobs.json"

RECEIVED = "received"
CLOUD_PENDING = "cloud_pending"
READY = "ready"
DONE = "done"  # abdm owns local progress once handed off, so handed_off == done here
FAILED = "failed"

POLL_DELTAS = (0, 5, 25)  # polls land at 0s/5s/30s, then every 10s
TERMINAL = (DONE, FAILED)


def _btih(magnet: str) -> str | None:
    for part in magnet.split("?", 1)[1].split("&"):
        if part.startswith("xt=urn:btih:"):
            return part[len("xt=urn:btih:"):].lower()
    return None


@dataclass
class Job:
    source: str
    folder: str
    state: str = RECEIVED
    torrent_id: int | None = None
    files: list = field(default_factory=list)
    handed: int = 0
    polls: int = 0
    error: str | None = None
    job_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])


class Orchestrator:
    def __init__(self, tb=None, adm=None):
        self.tb = tb if tb is not None else torbox.TorBoxClient(config.get_torbox_key())
        self.adm = adm if adm is not None else abdm.AbdmClient()
        self.jobs = {}
        self._load()

    def _load(self):
        try:
            raw = json.loads(JOBS_FILE.read_text())
        except (OSError, ValueError):
            return
        for d in raw:
            j = Job(**d)
            # a restart mid-flight can't know what happened; poll again
            if j.state not in (DONE, FAILED):
                j.state = CLOUD_PENDING if j.torrent_id else RECEIVED
            self.jobs[j.job_id] = j

    def save(self):
        # temp + replace: a crash mid-write must not truncate the real file
        JOBS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = JOBS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps([asdict(j) for j in self.jobs.values()], indent=2))
        os.replace(tmp, JOBS_FILE)

    def submit(self, folder: str, magnet: str | None = None,
               torrent_path: str | None = None) -> Job:
        j = Job(source=magnet or str(torrent_path), folder=folder)
        self.jobs[j.job_id] = j
        try:
            data = self.tb.create(magnet=magnet, torrent_path=torrent_path)
        except torbox.TorBoxError as e:
            if magnet and self._reconcile(j, magnet):
                j.state = CLOUD_PENDING
            else:
                j.state = FAILED
                j.error = str(e)
            self.save()
            return j
        j.torrent_id = data.get("torrent_id") if isinstance(data, dict) else None
        if j.torrent_id is None:
            j.state = FAILED
            j.error = "createtorrent returned no torrent_id"
        else:
            j.state = CLOUD_PENDING
        self.save()
        return j

    def resume_or_submit(self, source: str, folder: str) -> Job:
        # a re-click of the same source must resume, never double-add
        job = self.match(source)
        if job is None:
            if source.startswith("magnet:"):
                job = self.submit(folder, magnet=source)
            else:
                job = self.submit(folder, torrent_path=source)
        return job

    def match(self, source: str) -> Job | None:
        # the job this source would resume, or None if a fresh add is needed
        job = next(
            (j for j in self.jobs.values()
             if j.source == source and j.state not in TERMINAL),
            None,
        )
        if job is None:
            job = next(
                (j for j in self.jobs.values()
                 if j.source == source and j.state == FAILED),
                None,
            )
            if job is not None:
                if job.torrent_id:
                    # a failed hand-off with a live torrent_id retries
                    job.state = READY if job.files else CLOUD_PENDING
                elif source.startswith("magnet:") and self._reconcile(job, source):
                    job.state = CLOUD_PENDING
                else:
                    # the add never landed server-side; a fresh submit is safe
                    job = None
                if job is not None:
                    job.error = None
        if job is None:
            # a done job is returned as-is: re-adding would land a duplicate
            # cloud item that torbox cooldowns us for deleting
            job = next(
                (j for j in self.jobs.values()
                 if j.source == source and j.state == DONE),
                None,
            )
        return job

    def _reconcile(self, j: Job, magnet: str) -> bool:
        # a timed-out add may have landed: adopt the cloud item by infohash
        btih = _btih(magnet)
        if not btih:
            return False
        try:
            items = self.tb.mylist()
        except torbox.TorBoxError:
            return False
        if isinstance(items, dict):
            items = [items]
        for it in items:
            if (it.get("hash") or "").lower() == btih:
                j.torrent_id = it.get("id")
                return True
        return False

    def next_delay(self, j: Job) -> int:
        return POLL_DELTAS[j.polls] if j.polls < len(POLL_DELTAS) else 10

    def tick(self):
        for j in list(self.jobs.values()):
            if j.state == CLOUD_PENDING:
                self._poll(j)
            elif j.state == READY:
                self._handoff(j)
        self.save()

    def _poll(self, j: Job):
        j.polls += 1
        try:
            items = self.tb.mylist(torrent_id=j.torrent_id)
        except torbox.TorBoxError as e:
            j.error = str(e)  # transient; stay pending, keep last error for surfacing
            return
        if isinstance(items, dict):  # mylist?id= returns the object, not a list
            items = [items]
        it = items[0] if items else None
        if not it:
            return
        if it.get("download_finished") or (it.get("progress") or 0) >= 1:
            j.files = it.get("files") or []
            j.state = READY

    def _handoff(self, j: Job):
        # resume from j.handed so a crashed retry never double-adds to abdm
        try:
            for f in j.files[j.handed:]:
                link = self.tb.requestdl(j.torrent_id, f["id"])
                # abdm rejects '/' in names; torbox nests subfolders in them
                name = (f.get("name") or "").rsplit("/", 1)[-1]
                self.adm.handoff(link, j.folder, name=name)
                j.handed += 1
                # persist per file: a crash here must not replay handed files
                self.save()
        except (torbox.TorBoxError, abdm.AbdmError) as e:
            j.state = FAILED
            j.error = str(e)
            return
        j.state = DONE
