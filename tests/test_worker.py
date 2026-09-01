from concierge import orchestrator as orch
from concierge import worker


class _FakeOrch:
    def __init__(self, states):
        self.states = states
        self.job = orch.Job(source="magnet:?xt=urn:btih:x", folder="C:/dl",
                            state=states[0])
        self.ticks = 0

    def resume_or_submit(self, source, folder):
        return self.job

    def next_delay(self, job):
        return 0

    def tick(self):
        self.ticks += 1
        self.job.state = self.states[min(self.ticks, len(self.states) - 1)]


def test_run_drives_pending_to_done():
    rc = worker.run("m", "C:/dl", o=_FakeOrch([orch.CLOUD_PENDING, orch.DONE]))
    assert rc == 0


def test_run_stops_on_failed():
    rc = worker.run("m", "C:/dl", o=_FakeOrch([orch.CLOUD_PENDING, orch.FAILED]))
    assert rc == 1


def test_run_terminal_job_never_ticks():
    fake = _FakeOrch([orch.DONE])
    rc = worker.run("m", "C:/dl", o=fake)
    assert rc == 0
    assert fake.ticks == 0


def test_main_parses_source_and_folder(monkeypatch):
    seen = {}

    def fake_run(source, folder, o=None):
        seen.update(source=source, folder=folder)
        return 0

    monkeypatch.setattr(worker, "run", fake_run)
    rc = worker.main(["--source", "magnet:?xt=urn:btih:x", "--folder", "C:/dl"])
    assert rc == 0
    assert seen == {"source": "magnet:?xt=urn:btih:x", "folder": "C:/dl"}
