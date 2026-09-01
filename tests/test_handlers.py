import subprocess

from concierge import handlers
from concierge import orchestrator as orch


class _StubJob:
    def __init__(self, state, source="magnet:?xt=urn:btih:x"):
        self.state = state
        self.source = source
        self.job_id = "x"
        self.error = None if state == orch.DONE else "boom"


class _StubOrch:
    def __init__(self, first_state=orch.DONE):
        self.resumed = []
        self.first_state = first_state
        self.job = None
        self.ticks = 0

    def resume_or_submit(self, source, folder):
        self.resumed.append((source, folder))
        self.job = _StubJob(self.first_state)
        return self.job

    def next_delay(self, job):
        return 0

    def tick(self):
        self.ticks += 1
        if self.job is not None:
            self.job.state = orch.DONE


def test_magnet_runs_to_done(monkeypatch):
    so = _StubOrch()
    monkeypatch.setattr(orch, "Orchestrator", lambda: so)
    rc = handlers.main(["magnet:?xt=urn:btih:x", "--folder", "C:/dl"])
    assert rc == 0
    assert so.resumed == [("magnet:?xt=urn:btih:x", "C:/dl")]


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


def test_missing_file_exit_two(monkeypatch, tmp_path):
    rc = handlers.main([str(tmp_path / "nope.torrent"), "--folder", "C:/dl"])
    assert rc == 2


def test_detach_spawns_worker_and_returns(monkeypatch):
    spawned = []

    class _FakePopen:
        def __init__(self, argv, **kw):
            spawned.append((argv, kw))

    monkeypatch.setattr(subprocess, "Popen", _FakePopen)
    so = _StubOrch()
    monkeypatch.setattr(orch, "Orchestrator", lambda: so)
    rc = handlers.main(["magnet:?xt=urn:btih:x", "--folder", "C:/dl", "--detach"])
    assert rc == 0
    assert so.ticks == 0  # the worker polls, not this process
    (argv, kw), = spawned
    assert argv[1:3] == ["-m", "concierge.worker"]
    assert argv[argv.index("--source") + 1] == "magnet:?xt=urn:btih:x"
    assert argv[argv.index("--folder") + 1] == "C:/dl"
    assert kw["creationflags"] == handlers._DETACH_FLAGS


def test_detach_missing_file_still_exit_two(monkeypatch, tmp_path):
    spawned = []
    monkeypatch.setattr(subprocess, "Popen", lambda argv, **kw: spawned.append(argv))
    rc = handlers.main([str(tmp_path / "nope.torrent"), "--folder", "C:/dl", "--detach"])
    assert rc == 2
    assert spawned == []
