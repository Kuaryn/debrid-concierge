import runpy
import subprocess
import sys
from pathlib import Path

import pytest

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
    assert kw["stdin"] == subprocess.DEVNULL
    assert kw["env"] is None


def test_detach_uses_packaged_worker(monkeypatch, tmp_path):
    spawned = []

    class _FakePopen:
        def __init__(self, argv, **kw):
            spawned.append((argv, kw))

    monkeypatch.setattr(subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(handlers.sys, "frozen", True, raising=False)
    package_dir = tmp_path / "concierge"
    handler_exe = package_dir / "debrid-concierge-handler.exe"
    monkeypatch.setattr(handlers.sys, "executable", str(handler_exe))
    rc = handlers.main(["magnet:?xt=urn:btih:x", "--detach"])
    assert rc == 0
    (argv, kw), = spawned
    assert argv[0] == str(package_dir / "debrid-concierge-worker.exe")
    assert kw["env"]["PYINSTALLER_RESET_ENVIRONMENT"] == "1"


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


@pytest.mark.parametrize("detach", [False, True])
@pytest.mark.parametrize("source", [
    'magnet:?dn="private"',
    "magnet:?dn=line\rbreak",
    "magnet:?dn=line\nbreak",
    "magnet:?dn=tab\tname",
    "magnet:?dn=null\x00name",
    "magnet:?dn=delete\x7fname",
    "magnet:?dn=control\x85name",
    "--folder=private",
])
def test_unsafe_source_stops_before_worker(monkeypatch, capsys, source, detach):
    def unexpected(*args, **kwargs):
        pytest.fail("unsafe source reached the worker or filesystem")

    monkeypatch.setattr(subprocess, "Popen", unexpected)
    monkeypatch.setattr(worker, "run", unexpected)
    monkeypatch.setattr(handlers.os.path, "isfile", unexpected)
    args = ["--detach"] if detach else []
    with pytest.raises(SystemExit) as exc:
        handlers.main([*args, "--", source])
    assert exc.value.code == 2
    output = capsys.readouterr()
    assert "invalid source" in output.err
    assert source not in output.out + output.err


@pytest.mark.parametrize("torrent", [False, True])
def test_registry_entry_ignores_extra_options(monkeypatch, tmp_path, capsys, torrent):
    source = "magnet:?xt=urn:btih:x&dn=Alice's%20file&tr=https%3A%2F%2Ftracker.invalid"
    if torrent:
        path = tmp_path / "Alice's file with spaces.torrent"
        path.write_bytes(b"")
        source = str(path)
    spawned = []
    monkeypatch.setattr(subprocess, "Popen", lambda argv, **kw: spawned.append(argv))
    monkeypatch.setattr(sys, "argv", ["win_handler.py", source, "--folder",
                                    str(tmp_path / "Startup")])
    monkeypatch.setattr(sys, "path", sys.path.copy())
    with pytest.raises(SystemExit) as exc:
        runpy.run_path(str(Path(__file__).resolve().parents[1] / "win_handler.py"))
    assert exc.value.code == 0
    assert spawned == [handlers._worker_command(source)]
    output = capsys.readouterr()
    assert source not in output.out + output.err


@pytest.mark.parametrize("args", [[], ["--help"], ["--folder=private"]])
def test_registry_entry_requires_a_source(monkeypatch, args):
    def unexpected(*args, **kwargs):
        pytest.fail("invalid registry arguments launched a worker")

    monkeypatch.setattr(subprocess, "Popen", unexpected)
    monkeypatch.setattr(sys, "argv", ["win_handler.py", *args])
    monkeypatch.setattr(sys, "path", sys.path.copy())
    with pytest.raises(SystemExit) as exc:
        runpy.run_path(str(Path(__file__).resolve().parents[1] / "win_handler.py"))
    assert exc.value.code == 2


def test_detach_does_not_print_magnet(monkeypatch, capsys):
    source = "magnet:?xt=urn:btih:x&tr=https%3A%2F%2Ftracker.invalid%2Fprivate-tracker-value"
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kw: None)
    assert handlers.main([source, "--detach"]) == 0
    output = capsys.readouterr()
    assert source not in output.out + output.err
    assert "private-tracker-value" not in output.out + output.err
