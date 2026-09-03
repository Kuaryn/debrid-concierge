"""Job state machine: cloud add, poll to completion, per-file hand-off to ABDM."""

import base64
import binascii
import json
import math
import os
import uuid
from dataclasses import asdict, dataclass, field
from urllib.parse import parse_qsl, urlsplit

from concierge import abdm, config
from concierge.providers import torbox

JOBS_FILE = config.APP_DIR / "jobs.json"

RECEIVED = "received"
CLOUD_PENDING = "cloud_pending"
READY = "ready"
DONE = "done"  # abdm owns local progress once handed off, so handed_off == done here
FAILED = "failed"

POLL_DELTAS = (0, 5, 25)  # polls land at 0s/5s/30s, then every 10s
MAX_POLL_ERRORS = 6
MAX_MISSING_POLLS = 3
TERMINAL = (DONE, FAILED)
KEEP_TERMINAL = 20  # finished jobs double as click-dedupe, keep the newest 20
WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                    *(f"LPT{i}" for i in range(1, 10))}


def _normalize_btih(value) -> str | None:
    if not isinstance(value, str):
        return None
    if len(value) == 40 and all(c in "0123456789abcdefABCDEF" for c in value):
        return value.lower()
    if len(value) != 32:
        return None
    try:
        decoded = base64.b32decode(value, casefold=True)
    except (binascii.Error, ValueError):
        return None
    return decoded.hex() if len(decoded) == 20 else None


def _btih(magnet: str) -> str | None:
    try:
        parsed = urlsplit(magnet)
        params = parse_qsl(parsed.query, keep_blank_values=True, max_num_fields=100)
    except ValueError:
        return None
    if parsed.scheme.lower() != "magnet":
        return None
    for key, value in params:
        if key.lower() != "xt" or not value.lower().startswith("urn:btih:"):
            continue
        btih = _normalize_btih(value[len("urn:btih:"):])
        if btih:
            return btih
    return None


def _same_source(saved: str, source: str) -> bool:
    saved_btih = _btih(saved)
    source_btih = _btih(source)
    if saved_btih is not None and source_btih is not None:
        return saved_btih == source_btih
    return saved == source


def _file_name(value) -> str | None:
    if not isinstance(value, str):
        return None
    name = value.replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join("_" if ord(c) < 32 or c in '<>:"/\\|?*' else c for c in name)
    name = name.strip(" .")[:240].rstrip(" .")
    if not name or name in {".", ".."}:
        return None
    if name.split(".", 1)[0].upper() in WINDOWS_RESERVED:
        name = "_" + name
    return name


@dataclass
class Job:
    source: str
    folder: str
    state: str = RECEIVED
    torrent_id: int | None = None
    files: list = field(default_factory=list)
    handed: int = 0
    polls: int = 0
    next_poll_at: float = 0
    poll_errors: int = 0
    missing_polls: int = 0
    error: str | None = None
    job_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])


def _load_job(value) -> Job | None:
    if not isinstance(value, dict) or any(k not in Job.__dataclass_fields__ for k in value):
        return None
    try:
        job = Job(**value)
    except TypeError:
        return None
    if (not isinstance(job.source, str) or not job.source
            or not isinstance(job.folder, str) or not job.folder
            or not isinstance(job.state, str)
            or job.state not in {RECEIVED, CLOUD_PENDING, READY, DONE, FAILED}
            or not isinstance(job.job_id, str) or not job.job_id):
        return None
    if job.torrent_id is not None and (type(job.torrent_id) is not int or job.torrent_id <= 0):
        return None
    if not isinstance(job.files, list) or any(not isinstance(f, dict) for f in job.files):
        return None
    counts = (job.handed, job.polls, job.poll_errors, job.missing_polls)
    if any(type(n) is not int or n < 0 for n in counts) or job.handed > len(job.files):
        return None
    if (isinstance(job.next_poll_at, bool) or not isinstance(job.next_poll_at, (int, float))
            or not math.isfinite(job.next_poll_at) or job.next_poll_at < 0):
        return None
    if job.error is not None and not isinstance(job.error, str):
        return None
    if job.state == CLOUD_PENDING and job.torrent_id is None:
        return None
    if job.state == RECEIVED:
        if job.torrent_id is None:
            return None
        job.state = CLOUD_PENDING
    return job


class JobsError(Exception):
    pass


class Orchestrator:
    def __init__(self, tb=None, adm=None):
        self.tb = tb if tb is not None else torbox.TorBoxClient(config.get_torbox_key())
        self.adm = adm if adm is not None else abdm.AbdmClient()
        self.jobs = {}
        self.load_warning = self.reload()

    def reload(self):
        self.jobs.clear()
        self.load_warning = self._load()
        return self.load_warning

    def _load(self):
        try:
            text = JOBS_FILE.read_text()
        except FileNotFoundError:
            return None
        except (OSError, UnicodeError):
            raise JobsError("saved jobs could not be read") from None
        try:
            raw = json.loads(text)
        except ValueError:
            raw = None
        rejected = not isinstance(raw, list)
        if isinstance(raw, list):
            for value in raw:
                job = _load_job(value)
                if job is None or job.job_id in self.jobs:
                    rejected = True
                    continue
                self.jobs[job.job_id] = job
        if not rejected:
            return None
        try:
            backup = config.preserve_bad(JOBS_FILE)
        except OSError:
            raise JobsError("invalid jobs file could not be preserved") from None
        self.save()
        return f"Some saved jobs were invalid. The original is in {backup.name}."

    def _prune(self):
        terminal = [j for j in self.jobs.values() if j.state in TERMINAL]
        excess = len(terminal) - KEEP_TERMINAL
        if excess > 0:
            for j in terminal[:excess]:
                del self.jobs[j.job_id]

    def save(self):
        self._prune()
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
             if _same_source(j.source, source) and j.state not in TERMINAL),
            None,
        )
        if job is None:
            job = next(
                (j for j in self.jobs.values()
                 if _same_source(j.source, source) and j.state == FAILED),
                None,
            )
            if job is not None:
                if job.torrent_id:
                    # a failed hand-off with a live torrent_id retries
                    job.state = READY if job.files else CLOUD_PENDING
                    job.next_poll_at = 0
                    job.poll_errors = 0
                    job.missing_polls = 0
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
                 if _same_source(j.source, source) and j.state == DONE),
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
            if isinstance(it, dict) and _normalize_btih(it.get("hash")) == btih:
                j.torrent_id = it.get("id")
                return True
        return False

    def next_delay(self, j: Job) -> int:
        return POLL_DELTAS[j.polls] if j.polls < len(POLL_DELTAS) else 10

    def tick(self, now: float | None = None):
        for j in list(self.jobs.values()):
            if j.state == CLOUD_PENDING:
                if now is not None and j.next_poll_at > now:
                    continue
                self._poll(j)
                if now is not None and j.state == CLOUD_PENDING:
                    j.next_poll_at = now + self.next_delay(j)
            elif j.state == READY:
                self._handoff(j)
        self.save()

    def _poll(self, j: Job):
        j.polls += 1
        try:
            items = self.tb.mylist(torrent_id=j.torrent_id)
        except torbox.TorBoxError as e:
            j.poll_errors += 1
            j.error = str(e)
            if j.poll_errors >= MAX_POLL_ERRORS:
                j.state = FAILED
            return
        j.poll_errors = 0
        if isinstance(items, dict):  # mylist?id= returns the object, not a list
            items = [items]
        elif not isinstance(items, list):
            j.state = FAILED
            j.error = "torbox returned an invalid torrent list"
            return
        it = items[0] if items else None
        if not it:
            j.missing_polls += 1
            j.error = "torbox did not return this torrent"
            if j.missing_polls >= MAX_MISSING_POLLS:
                j.state = FAILED
            return
        if not isinstance(it, dict):
            j.state = FAILED
            j.error = "torbox returned an invalid torrent"
            return
        j.missing_polls = 0
        j.error = None
        progress = it.get("progress")
        if progress is not None and (isinstance(progress, bool)
                                     or not isinstance(progress, (int, float))
                                     or not 0 <= progress <= 1):
            j.state = FAILED
            j.error = "torbox returned invalid progress"
            return
        if it.get("download_finished") is True or (progress or 0) >= 1:
            files = it.get("files")
            if not isinstance(files, list) or not files:
                j.state = FAILED
                j.error = "completed torrent returned no files"
                return
            if any(not isinstance(f, dict) or f.get("id") is None for f in files):
                j.state = FAILED
                j.error = "torbox returned an invalid file list"
                return
            j.files = files
            j.state = READY

    def _handoff(self, j: Job):
        # resume from j.handed so a crashed retry never double-adds to abdm
        try:
            if not j.files:
                raise torbox.TorBoxError("completed torrent returned no files")
            if j.handed < len(j.files):
                f = j.files[j.handed]
                if not isinstance(f, dict) or f.get("id") is None:
                    raise torbox.TorBoxError("torbox returned an invalid file list")
                link = self.tb.requestdl(j.torrent_id, f["id"])
                name = _file_name(f.get("name"))
                self.adm.handoff(link, j.folder, name=name)
                j.handed += 1
                # persist per file: a crash here must not replay handed files
                self.save()
        except (torbox.TorBoxError, abdm.AbdmError) as e:
            j.state = FAILED
            j.error = str(e)
            return
        if j.handed >= len(j.files):
            j.state = DONE
