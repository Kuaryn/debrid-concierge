import sys
from pathlib import Path

# repo-root scripts (repoint, win_* entries) are test targets too
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from concierge import abdm, config
from concierge import orchestrator as orch


@pytest.fixture(autouse=True)
def _isolate_user_files(monkeypatch, tmp_path):
    app_dir = tmp_path / "app"
    monkeypatch.setattr(config, "APP_DIR", app_dir)
    monkeypatch.setattr(config, "CONFIG_FILE", app_dir / "config.json")
    monkeypatch.setattr(orch, "JOBS_FILE", app_dir / "jobs.json")
    monkeypatch.setattr(abdm, "SETTINGS", tmp_path / "abdm" / "appSettings.json")
