from concierge import abdm, config
from concierge import orchestrator as orch


def test_user_paths_are_temporary(tmp_path):
    paths = (config.APP_DIR, config.CONFIG_FILE, orch.JOBS_FILE, abdm.SETTINGS)
    assert all(path.is_relative_to(tmp_path) for path in paths)
