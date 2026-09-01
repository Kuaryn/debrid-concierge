import subprocess

import repoint


def test_repoint_writes_all_four_keys(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda args, **kw: calls.append(args))
    assert repoint.main() == 0
    keys = [c[2] for c in calls]
    assert keys == [
        r"HKCU\Software\Classes\magnet\shell\open\command",
        r"HKCU\Software\Classes\debrid-concierge.torrent\shell\open\command",
        r"HKCU\Software\Classes\.torrent",
        r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run",
    ]
    assert "win_handler.py" in calls[0][7] and "%1" in calls[0][7]
    assert "win_tray.py" in calls[3][8]
