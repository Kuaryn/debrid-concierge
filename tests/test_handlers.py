from concierge import handlers
from concierge import orchestrator as orch


class _StubJob:
    def __init__(self, state, source="magnet:?xt=urn:btih:x", torrent_id=None, files=None):
        self.state = state
        self.source = source
        self.job_id = "x"
        self.torrent_id = torrent_id
        self.files = files or []
        self.error = None if state == orch.DONE else "boom"
        self.polls = 0


class _StubOrch:
    def __init__(self, first_state=orch.DONE):
        self.submitted = []
        self.first_state = first_state
        self.job = None
        self.ticks = 0
        self.jobs = {}

    def submit(self, folder, magnet=None, torrent_path=None):
        self.submitted.append((folder, magnet, torrent_path))
        self.job = _StubJob(self.first_state)
        return self.job

    def next_delay(self, job):
        return 0

    def tick(self):
        self.ticks += 1
        if self.job is not None:
            self.job.state = orch.DONE
        for j in self.jobs.values():
            j.state = orch.DONE


def test_magnet_runs_to_done(monkeypatch):
    so = _StubOrch()
    monkeypatch.setattr(orch, "Orchestrator", lambda: so)
    rc = handlers.main(["magnet:?xt=urn:btih:x", "--folder", "C:/dl"])
    assert rc == 0
    assert so.submitted == [("C:/dl", "magnet:?xt=urn:btih:x", None)]


def test_pending_job_loops_until_done(monkeypatch):
    so = _StubOrch(first_state=orch.CLOUD_PENDING)
    monkeypatch.setattr(orch, "Orchestrator", lambda: so)
    rc = handlers.main(["magnet:?xt=urn:btih:x", "--folder", "C:/dl"])
    assert rc == 0
    assert so.ticks == 1


def test_failed_job_exit_one(monkeypatch):
    so = _StubOrch(first_state=orch.FAILED)
    monkeypatch.setattr(orch, "Orchestrator", lambda: so)
    rc = handlers.main(["magnet:?xt=urn:btih:x", "--folder", "C:/dl"])
    assert rc == 1


def test_resume_skips_second_submit(monkeypatch):
    so = _StubOrch()
    so.jobs["old"] = _StubJob(orch.CLOUD_PENDING)
    monkeypatch.setattr(orch, "Orchestrator", lambda: so)
    rc = handlers.main(["magnet:?xt=urn:btih:x", "--folder", "C:/dl"])
    assert rc == 0
    assert so.submitted == []
    assert so.ticks == 1


def test_failed_handoff_retries_without_readd(monkeypatch):
    so = _StubOrch()
    so.jobs["old"] = _StubJob(orch.FAILED, torrent_id=7, files=[{"id": 1, "name": "a.mkv"}])
    monkeypatch.setattr(orch, "Orchestrator", lambda: so)
    rc = handlers.main(["magnet:?xt=urn:btih:x", "--folder", "C:/dl"])
    assert rc == 0
    assert so.submitted == []
    assert so.ticks == 1


def test_missing_file_exit_two(monkeypatch, tmp_path):
    rc = handlers.main([str(tmp_path / "nope.torrent"), "--folder", "C:/dl"])
    assert rc == 2
