import subprocess

import repoint


def test_repoint_writes_all_four_keys(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda args, **kw: calls.append(args))
    assert repoint.main() == 0
    keys = [c[2] for c in calls]
    assert keys == [
        r"HKCU\Software\Classes\magnet\shell\open\command",
        r"HKCU\Software\Classes\debrid-concierge.torrent",
        r"HKCU\Software\Classes\debrid-concierge.torrent\shell\open\command",
        r"HKCU\Software\Classes\.torrent",
        r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run",
    ]
    assert calls[1][7] == "Debrid Concierge"
    assert "win_handler.py" in calls[2][7] and "%1" in calls[2][7]
    assert "win_tray.py" in calls[4][8]


def test_package_dir_points_at_the_exes(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda args, **kw: calls.append(args))
    for name in ("debrid-concierge-handler.exe", "debrid-concierge-worker.exe",
                 "debrid-concierge-tray.exe"):
        (tmp_path / name).touch()
    assert repoint.main(tmp_path) == 0
    pkg = tmp_path.resolve()
    assert calls[0][7] == f'"{pkg / "debrid-concierge-handler.exe"}" "%1"'
    assert calls[2][7] == calls[0][7]
    assert calls[4][8] == f'"{pkg / "debrid-concierge-tray.exe"}"'


def test_missing_exe_writes_nothing(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda args, **kw: calls.append(args))
    (tmp_path / "debrid-concierge-handler.exe").touch()
    assert repoint.main(tmp_path) == 1
    assert calls == []
