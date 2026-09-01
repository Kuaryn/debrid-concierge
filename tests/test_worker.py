from concierge import lock, worker
from concierge import orchestrator as orch


class _FakeOrch:
    def __init__(self, states_by_source):
        self.jobs = {s: orch.Job(source=s, folder="C:/dl", state=st[0])
                     for s, st in states_by_source.items()}
        self._states = states_by_source
        self.ticks = 0
        self.submitted = []

    def match(self, source):
        return self.jobs.get(source)

    def resume_or_submit(self, source, folder):
        if source in self.jobs:
            return self.jobs[source]
        j = orch.Job(source=source, folder=folder, state=orch.DONE)
        self.jobs[source] = j
        self.submitted.append((source, folder))
        return j

    def next_delay(self, job):
        return 0

    def tick(self):
        self.ticks += 1
        for s, j in self.jobs.items():
            st = self._states[s]
            j.state = st[min(self.ticks, len(st) - 1)]


def test_run_drains_all_jobs():
    fake = _FakeOrch({"a": [orch.CLOUD_PENDING, orch.DONE],
                      "b": [orch.READY, orch.DONE]})
    job = worker.run("a", "C:/dl", o=fake)
    assert job is fake.jobs["a"]
    assert all(j.state == orch.DONE for j in fake.jobs.values())
    assert fake.ticks >= 1


def test_run_returns_failed_job():
    fake = _FakeOrch({"a": [orch.CLOUD_PENDING, orch.FAILED]})
    job = worker.run("a", "C:/dl", o=fake)
    assert job.state == orch.FAILED


def test_run_terminal_job_never_ticks():
    fake = _FakeOrch({"a": [orch.DONE]})
    job = worker.run("a", "C:/dl", o=fake)
    assert job.state == orch.DONE
    assert fake.ticks == 0


def test_main_exit_codes(monkeypatch):
    done = orch.Job(source="m", folder="C:/dl", state=orch.DONE)
    monkeypatch.setattr(worker, "run", lambda s, f, o=None: done)
    assert worker.main(["--source", "m", "--folder", "C:/dl"]) == 0
    failed = orch.Job(source="m", folder="C:/dl", state=orch.FAILED)
    monkeypatch.setattr(worker, "run", lambda s, f, o=None: failed)
    assert worker.main(["--source", "m", "--folder", "C:/dl"]) == 1


def test_worker_lock_yields():
    with lock.worker_lock():
        assert True


def test_dialog_cancel_exits_without_job(monkeypatch):
    monkeypatch.setattr(worker.dialog, "ask_folder", lambda initial=None: None)
    fake = _FakeOrch({})
    assert worker.run("m", None, o=fake) is None
    assert fake.submitted == []


def test_dialog_choice_used_and_remembered(monkeypatch):
    monkeypatch.setattr(worker.dialog, "ask_folder", lambda initial=None: "C:/picked")
    monkeypatch.setattr(worker.config, "load", lambda: {"last_folder": None})
    saved = {}
    monkeypatch.setattr(worker.config, "save", lambda cfg: saved.update(cfg))
    fake = _FakeOrch({})
    job = worker.run("m", None, o=fake)
    assert job.folder == "C:/picked"
    assert saved["last_folder"] == "C:/picked"


def test_existing_job_skips_dialog(monkeypatch):
    def boom(initial=None):
        raise AssertionError("dialog must not open for a resumable job")

    monkeypatch.setattr(worker.dialog, "ask_folder", boom)
    fake = _FakeOrch({"m": [orch.CLOUD_PENDING, orch.DONE]})
    job = worker.run("m", None, o=fake)
    assert job.state == orch.DONE
