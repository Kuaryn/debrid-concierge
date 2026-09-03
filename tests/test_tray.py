from concierge import orchestrator as orch
from concierge import tray


def _job(jid, state, error=None):
    return {"job_id": jid, "state": state, "error": error}


def test_tooltip_idle():
    assert tray.tooltip([]) == "concierge: idle"
    assert tray.tooltip([_job("a", orch.DONE)]) == "concierge: idle"


def test_tooltip_counts_active():
    jobs = [_job("a", orch.CLOUD_PENDING), _job("b", orch.READY), _job("c", orch.DONE)]
    assert tray.tooltip(jobs) == "concierge: 2 active"


def test_toast_on_done_transition():
    events = tray.toast_events({"a": orch.READY}, [_job("a", orch.DONE)])
    assert events == [("concierge", "a handed to abdm")]


def test_toast_on_failure_includes_error():
    events = tray.toast_events({"a": orch.CLOUD_PENDING}, [_job("a", orch.FAILED, "boom")])
    assert events == [("concierge failed", "a: boom")]


def test_toast_does_not_show_magnet_source():
    job = _job("a", orch.DONE)
    job["source"] = "magnet:?xt=urn:btih:secret&tr=https://tracker.example"
    events = tray.toast_events({"a": orch.READY}, [job])
    assert job["source"] not in " ".join(events[0])


def test_no_toast_on_first_sight():
    assert tray.toast_events({}, [_job("a", orch.FAILED, "boom")]) == []


def test_no_toast_when_unchanged():
    assert tray.toast_events({"a": orch.DONE}, [_job("a", orch.DONE)]) == []


def test_toast_after_manual_retry():
    # a failed job flipped back and completed is a fresh outcome
    events = tray.toast_events({"a": orch.FAILED}, [_job("a", orch.DONE)])
    assert len(events) == 1


def test_load_jobs_missing_file(monkeypatch, tmp_path):
    monkeypatch.setattr(orch, "JOBS_FILE", tmp_path / "jobs.json")
    assert tray.load_jobs() == []


def test_load_jobs_reads_file(monkeypatch, tmp_path):
    (tmp_path / "jobs.json").write_text('[{"job_id": "a", "state": "done"}]')
    monkeypatch.setattr(orch, "JOBS_FILE", tmp_path / "jobs.json")
    assert tray.load_jobs() == [{"job_id": "a", "state": "done"}]


def test_load_jobs_ignores_wrong_root(monkeypatch, tmp_path):
    (tmp_path / "jobs.json").write_text("7")
    monkeypatch.setattr(orch, "JOBS_FILE", tmp_path / "jobs.json")
    assert tray.load_jobs() == []
