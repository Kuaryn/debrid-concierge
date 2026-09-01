import subprocess

from concierge import handlers, worker
from concierge import orchestrator as orch


class _StubJob:
    def __init__(self, state, source="magnet:?xt=urn:btih:x"):
        self.state = state
        self.source = source
        self.job_id = "x"
        self.folder = "C:/dl"
        self.error = None if state == orch.DONE else "boom"


def _patch_run(monkeypatch, state):
    calls = []

    def fake_run(source, folder, o=None):
        calls.append((source, folder))
        return _StubJob(state)

    monkeypatch.setattr(worker, "run", fake_run)
    return calls


def test_magnet_runs_to_done(monkeypatch):
    calls = _patch_run(monkeypatch, orch.DONE)
    rc = handlers.main(["magnet:?xt=urn:btih:x", "--folder", "C:/dl"])
    assert rc == 0
    assert calls == [("magnet:?xt=urn:btih:x", "C:/dl")]


def test_failed_job_exit_one(monkeypatch):
    _patch_run(monkeypatch, orch.FAILED)
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
    calls = _patch_run(monkeypatch, orch.DONE)
    rc = handlers.main(["magnet:?xt=urn:btih:x", "--folder", "C:/dl", "--detach"])
    assert rc == 0
    assert calls == []  # the worker resumes, not this process
    (argv, kw), = spawned
    assert argv[1].endswith("win_worker.py")
    assert argv[argv.index("--source") + 1] == "magnet:?xt=urn:btih:x"
    assert argv[argv.index("--folder") + 1] == "C:/dl"
    assert kw["creationflags"] == handlers._DETACH_FLAGS


def test_detach_missing_file_still_exit_two(monkeypatch, tmp_path):
    spawned = []
    monkeypatch.setattr(subprocess, "Popen", lambda argv, **kw: spawned.append(argv))
    rc = handlers.main([str(tmp_path / "nope.torrent"), "--folder", "C:/dl", "--detach"])
    assert rc == 2
    assert spawned == []


def test_detach_without_folder_omits_flag(monkeypatch):
    spawned = []

    class _FakePopen:
        def __init__(self, argv, **kw):
            spawned.append(argv)

    monkeypatch.setattr(subprocess, "Popen", _FakePopen)
    _patch_run(monkeypatch, orch.DONE)
    rc = handlers.main(["magnet:?xt=urn:btih:x", "--detach"])
    assert rc == 0
    assert "--folder" not in spawned[0]


def test_blocking_without_folder_passes_none(monkeypatch):
    calls = _patch_run(monkeypatch, orch.DONE)
    rc = handlers.main(["magnet:?xt=urn:btih:x"])
    assert rc == 0
    assert calls == [("magnet:?xt=urn:btih:x", None)]
