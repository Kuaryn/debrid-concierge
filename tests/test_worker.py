from concierge import lock, worker
from concierge import orchestrator as orch


class _FakeOrch:
    def __init__(self, states_by_source):
        self.jobs = {s: orch.Job(source=s, folder="C:/dl", state=st[0])
                     for s, st in states_by_source.items()}
        self._states = states_by_source
        self.ticks = 0
        self.reloads = 0
        self.submitted = []

    def reload(self):
        self.reloads += 1

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

    def tick(self, now=None):
        self.ticks += 1
        for s, j in self.jobs.items():
            st = self._states[s]
            j.state = st[min(self.ticks, len(st) - 1)]
            if j.state not in orch.TERMINAL:
                j.next_poll_at = (now or 0) + 1


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


def test_worker_sleeps_without_holding_lock(monkeypatch):
    held = False
    now = 100
    sleeps = []

    class _TrackedLock:
        def __enter__(self):
            nonlocal held
            assert not held
            held = True

        def __exit__(self, *exc):
            nonlocal held
            held = False

    def sleep(seconds):
        nonlocal now
        assert not held
        sleeps.append(seconds)
        now += seconds

    monkeypatch.setattr(worker.lock, "worker_lock", _TrackedLock)
    monkeypatch.setattr(worker.time, "time", lambda: now)
    monkeypatch.setattr(worker.time, "sleep", sleep)
    fake = _FakeOrch({"a": [orch.CLOUD_PENDING, orch.CLOUD_PENDING, orch.DONE]})
    job = worker.run("a", "C:/dl", o=fake)
    assert job.state == orch.DONE
    assert sleeps == [1]
    assert fake.reloads >= 4


def test_worker_reloads_jobs_added_while_sleeping(monkeypatch):
    class _TB:
        def __init__(self):
            self.next_id = 1

        def create(self, **kw):
            torrent_id = self.next_id
            self.next_id += 1
            return {"torrent_id": torrent_id}

        def mylist(self, torrent_id=None):
            return {"progress": 1, "files": [{"id": torrent_id, "name": "file.mkv"}]}

        def requestdl(self, torrent_id, file_id):
            return f"https://cdn.example/{file_id}"

    class _Adm:
        def __init__(self):
            self.handed = []

        def handoff(self, link, folder, name=None):
            self.handed.append((link, folder, name))

    tb, adm = _TB(), _Adm()
    first = orch.Orchestrator(tb=tb, adm=adm)
    added = False

    def sleep(seconds):
        nonlocal added
        if not added:
            second = orch.Orchestrator(tb=tb, adm=adm)
            second.submit("C:/dl", magnet="magnet:b")
            added = True

    monkeypatch.setattr(worker.time, "sleep", sleep)
    job = worker.run("magnet:a", "C:/dl", o=first)
    saved = orch.Orchestrator(tb=tb, adm=adm)
    assert job.state == orch.DONE
    assert len(saved.jobs) == 2
    assert all(j.state == orch.DONE for j in saved.jobs.values())
    assert len(adm.handed) == 2


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
