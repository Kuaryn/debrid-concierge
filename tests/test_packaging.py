import runpy
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


@pytest.fixture
def checker():
    return runpy.run_path(Path(__file__).resolve().parents[1] / "packaging/check_bundle.py")


@pytest.fixture
def bundle(tmp_path):
    folder = tmp_path / "bundle with spaces"
    folder.mkdir()
    for role in ("handler", "tray", "worker"):
        (folder / f"debrid-concierge-{role}.exe").touch()
    warnings = tmp_path / "reports"
    warnings.mkdir()
    for role in ("handler", "tray", "worker"):
        (warnings / f"warn-{role}.txt").write_text(
            "missing module named pwd - imported by pystray (optional)\n")
    return folder, warnings


def test_bundle_check_accepts_unrelated_optional_imports(checker, bundle):
    checker["check_bundle"](*bundle)


@pytest.mark.parametrize("role", ["handler", "tray", "worker"])
def test_bundle_check_requires_each_executable(checker, bundle, role):
    folder, warnings = bundle
    exe = folder / f"debrid-concierge-{role}.exe"
    exe.unlink()
    exe.mkdir()
    with pytest.raises(ValueError, match=role):
        checker["check_bundle"](folder, warnings)


@pytest.mark.parametrize("role,module,status", [
    ("handler", "concierge.worker", "missing"),
    ("tray", "pystray._win32", "missing"),
    ("tray", "PIL.Image", "missing"),
    ("tray", "Pillow", "missing"),
    ("worker", "tkinter", "excluded"),
    ("worker", "_tkinter", "invalid"),
])
def test_bundle_check_rejects_missing_target_imports(checker, bundle, role, module, status):
    folder, warnings = bundle
    (warnings / f"warn-{role}.txt").write_text(
        f"{status} module named '{module}' - imported by app (top-level)\n")
    with pytest.raises(ValueError, match="unresolved"):
        checker["check_bundle"](folder, warnings)


@pytest.mark.parametrize("role", ["handler", "tray", "worker"])
def test_bundle_check_requires_warning_report(checker, bundle, role):
    folder, warnings = bundle
    (warnings / f"warn-{role}.txt").unlink()
    with pytest.raises(FileNotFoundError):
        checker["check_bundle"](folder, warnings)


def test_spec_keeps_each_analysis_warning_report(tmp_path, monkeypatch):
    conf = ModuleType("PyInstaller.config")
    conf.CONF = {"warnfile": str(tmp_path / "warn-debrid-concierge.txt")}
    monkeypatch.setitem(sys.modules, "PyInstaller", ModuleType("PyInstaller"))
    monkeypatch.setitem(sys.modules, "PyInstaller.config", conf)

    def analysis(scripts, **kwargs):
        role = Path(scripts[0]).stem.removeprefix("win_")
        Path(conf.CONF["warnfile"]).write_text(role)
        return SimpleNamespace(pure=[], scripts=[], binaries=[], datas=[])

    runpy.run_path(Path(__file__).resolve().parents[1] / "packaging/debrid-concierge.spec",
                  init_globals={"SPECPATH": str(tmp_path), "workpath": str(tmp_path),
                                "Analysis": analysis, "PYZ": lambda *a, **kw: None,
                                "EXE": lambda *a, **kw: None, "COLLECT": lambda *a, **kw: None})
    for role in ("handler", "tray", "worker"):
        assert (tmp_path / f"warn-{role}.txt").read_text() == role


def test_smokes_are_bounded_and_isolated(checker, bundle, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setenv("DEBRID_CONCIERGE_TEST_KEY", "test-only-placeholder")
    monkeypatch.setenv("PYTHONPATH", "must-not-be-inherited")
    monkeypatch.setenv("PYTHONHOME", "must-not-be-inherited")

    def run(cmd, **kwargs):
        calls.append(cmd)
        assert kwargs["timeout"] == 30
        assert kwargs["cwd"] == tmp_path
        for stream in ("stdin", "stdout", "stderr"):
            assert kwargs[stream] == subprocess.DEVNULL
        env = kwargs["env"]
        for key in ("APPDATA", "LOCALAPPDATA", "USERPROFILE", "HOME"):
            assert Path(env[key]).is_relative_to(tmp_path)
        for key in ("DEBRID_CONCIERGE_TEST_KEY", "PYTHONPATH", "PYTHONHOME"):
            assert key not in env
        assert env["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
        if cmd[0].endswith("handler.exe"):
            assert Path(cmd[1]).parent == tmp_path
            assert not Path(cmd[1]).exists()
            return subprocess.CompletedProcess(cmd, 2)
        assert cmd[1:] == ["--help"]
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", run)
    checker["smoke_bundle"](bundle[0], tmp_path)
    assert len(calls) == 2
    assert all("tray.exe" not in cmd[0] for cmd in calls)


@pytest.mark.parametrize("failure", ["exit", "timeout"])
def test_smoke_failure_stops_verification(checker, bundle, tmp_path, monkeypatch, failure):
    def run(cmd, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])
        return subprocess.CompletedProcess(cmd, 99)

    monkeypatch.setattr(subprocess, "run", run)
    expected = subprocess.TimeoutExpired if failure == "timeout" else ValueError
    with pytest.raises(expected):
        checker["smoke_bundle"](bundle[0], tmp_path)
