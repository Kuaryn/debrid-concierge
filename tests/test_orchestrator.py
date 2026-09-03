import json

import pytest

from concierge import abdm
from concierge import orchestrator as orch
from concierge.orchestrator import CLOUD_PENDING, DONE, FAILED, READY, RECEIVED, Job, Orchestrator
from concierge.providers.torbox import CooldownLimit, TorBoxError


class _StubTB:
    def __init__(self):
        self.created = None
        self.items = []
        self.create_raises = False
        self.list_error = False
        self.list_calls = 0

    def create(self, magnet=None, torrent_path=None, **kw):
        if self.create_raises:
            raise TorBoxError("torbox unreachable after 1 tries (timeout)")
        self.created = magnet or torrent_path
        return {"torrent_id": 7}

    def mylist(self, torrent_id=None):
        self.list_calls += 1
        if self.list_error:
            raise TorBoxError("torbox unreachable")
        return self.items

    def requestdl(self, torrent_id, file_id):
        return f"https://cdn.example/{file_id}"


class _StubAdm:
    def __init__(self, fail_times=0):
        self.handed = []
        self.fail_times = fail_times

    def handoff(self, link, folder, name=None, headers=None):
        if self.fail_times:
            self.fail_times -= 1
            raise abdm.AbdmDown("abdm unreachable (ConnectionError)")
        self.handed.append((link, folder, name))


class _FailSecond(_StubAdm):
    def handoff(self, link, folder, name=None, headers=None):
        if len(self.handed) == 1:
            raise abdm.AbdmDown("abdm unreachable (ConnectionError)")
        super().handoff(link, folder, name, headers)


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(orch, "JOBS_FILE", tmp_path / "jobs.json")
    tb, adm = _StubTB(), _StubAdm()
    return Orchestrator(tb=tb, adm=adm), tb, adm


def test_submit_moves_to_cloud_pending(env):
    o, tb, _ = env
    j = o.submit("C:/dl", magnet="magnet:?xt=urn:btih:abc")
    assert j.state == CLOUD_PENDING
    assert j.torrent_id == 7
    assert tb.created == "magnet:?xt=urn:btih:abc"


def test_submit_cooldown_fails(env):
    o, tb, _ = env

    def boom(**kw):
        raise CooldownLimit("cooldown until tomorrow", "2026-09-02T06:45:00Z")

    tb.create = boom
    j = o.submit("C:/dl", magnet="magnet:?xt=urn:btih:abc")
    assert j.state == FAILED
    assert "cooldown" in j.error


def test_poll_incomplete_stays_pending(env):
    o, tb, _ = env
    j = o.submit("C:/dl", magnet="m")
    tb.items = [{"progress": 0.5, "download_finished": False, "files": []}]
    o.tick()
    assert j.state == CLOUD_PENDING
    assert j.polls == 1


def test_poll_waits_until_due(env):
    o, tb, _ = env
    j = o.submit("C:/dl", magnet="m")
    j.next_poll_at = 20
    tb.items = [{"progress": 0.5, "download_finished": False, "files": []}]
    o.tick(now=10)
    assert j.polls == 0
    assert tb.list_calls == 0


def test_poll_records_next_due_time(env):
    o, tb, _ = env
    j = o.submit("C:/dl", magnet="m")
    tb.items = [{"progress": 0.5, "download_finished": False, "files": []}]
    o.tick(now=100)
    assert j.next_poll_at == 105


def test_poll_errors_eventually_fail(env):
    o, tb, _ = env
    j = o.submit("C:/dl", magnet="m")
    tb.list_error = True
    for _ in range(orch.MAX_POLL_ERRORS):
        o.tick()
    assert j.state == FAILED
    assert j.poll_errors == orch.MAX_POLL_ERRORS


def test_good_poll_resets_error_count(env):
    o, tb, _ = env
    j = o.submit("C:/dl", magnet="m")
    j.poll_errors = 2
    j.error = "old error"
    tb.items = [{"progress": 0.5, "download_finished": False, "files": []}]
    o.tick()
    assert j.poll_errors == 0
    assert j.error is None


def test_missing_torrent_eventually_fails(env):
    o, _, _ = env
    j = o.submit("C:/dl", magnet="m")
    for _ in range(orch.MAX_MISSING_POLLS):
        o.tick()
    assert j.state == FAILED
    assert j.missing_polls == orch.MAX_MISSING_POLLS
    assert j.error == "torbox did not return this torrent"


def test_poll_handles_dict_response(env):
    o, tb, _ = env
    j = o.submit("C:/dl", magnet="m")
    tb.items = {"progress": 1, "download_finished": True, "files": [{"id": 1, "name": "a.mkv"}]}
    o.tick()
    assert j.state == READY


def test_poll_rejects_non_object_item(env):
    o, tb, _ = env
    j = o.submit("C:/dl", magnet="m")
    tb.items = ["bad"]
    o.tick()
    assert j.state == FAILED
    assert j.error == "torbox returned an invalid torrent"


def test_poll_rejects_bad_progress(env):
    o, tb, _ = env
    j = o.submit("C:/dl", magnet="m")
    tb.items = [{"progress": "1", "files": []}]
    o.tick()
    assert j.state == FAILED
    assert j.error == "torbox returned invalid progress"


def test_poll_rejects_finished_without_files(env):
    o, tb, _ = env
    j = o.submit("C:/dl", magnet="m")
    tb.items = {"progress": 1, "files": []}
    o.tick()
    assert j.state == FAILED
    assert j.error == "completed torrent returned no files"


def test_poll_rejects_file_without_id(env):
    o, tb, _ = env
    j = o.submit("C:/dl", magnet="m")
    tb.items = {"progress": 1, "files": [{"name": "a.mkv"}]}
    o.tick()
    assert j.state == FAILED
    assert j.error == "torbox returned an invalid file list"


def test_multifile_poll_then_handoff(env):
    o, tb, adm = env
    j = o.submit("C:/dl", magnet="m")
    tb.items = [{"progress": 1, "download_finished": True,
                 "files": [{"id": 1, "name": "a.mkv"}, {"id": 2, "name": "b.mkv"}]}]
    o.tick()
    assert j.state == READY
    o.tick()
    assert j.state == READY
    o.tick()
    assert j.state == DONE
    assert [h[2] for h in adm.handed] == ["a.mkv", "b.mkv"]
    assert all(h[1] == "C:/dl" for h in adm.handed)


def test_handoff_strips_subfolder_from_name(env):
    o, _, adm = env
    j = Job(source="m", folder="C:/dl", state=READY, torrent_id=7,
            files=[{"id": 1, "name": "Big Buck Bunny/Big Buck Bunny.en.srt"}])
    o.jobs[j.job_id] = j
    o.tick()
    assert j.state == DONE
    assert adm.handed[0][2] == "Big Buck Bunny.en.srt"


def test_handoff_sanitizes_windows_name(env):
    o, _, adm = env
    j = Job(source="m", folder="C:/dl", state=READY, torrent_id=7,
            files=[{"id": 1, "name": r"..\..\Startup\evil?.exe"}])
    o.jobs[j.job_id] = j
    o.tick()
    assert j.state == DONE
    assert adm.handed[0][2] == "evil_.exe"


def test_handoff_rejects_empty_files(env):
    o, _, adm = env
    j = Job(source="m", folder="C:/dl", state=READY, torrent_id=7)
    o.jobs[j.job_id] = j
    o.tick()
    assert j.state == FAILED
    assert j.error == "completed torrent returned no files"
    assert adm.handed == []


def test_next_delay_schedule(env):
    o, _, _ = env
    j = Job(source="m", folder="C:/dl")
    delays = []
    for _ in range(5):
        delays.append(o.next_delay(j))
        j.polls += 1
    assert delays == [0, 5, 25, 10, 10]


def test_restart_recovers_pending(env):
    o, tb, _ = env
    o.submit("C:/dl", magnet="m")
    o2 = Orchestrator(tb=tb, adm=_StubAdm())
    (j,) = o2.jobs.values()
    assert j.state == CLOUD_PENDING
    assert j.torrent_id == 7


def test_restart_keeps_ready_job(env):
    o, tb, _ = env
    j = Job(source="m", folder="C:/dl", state=READY, torrent_id=7,
            files=[{"id": 1, "name": "a.mkv"}])
    o.jobs[j.job_id] = j
    o.save()
    o2 = Orchestrator(tb=tb, adm=_StubAdm())
    assert o2.jobs[j.job_id].state == READY


def test_old_job_gets_poll_defaults(env):
    _, tb, _ = env
    old = {"source": "m", "folder": "C:/dl", "state": CLOUD_PENDING, "torrent_id": 7}
    orch.JOBS_FILE.write_text(json.dumps([old]))
    loaded = Orchestrator(tb=tb, adm=_StubAdm())
    (job,) = loaded.jobs.values()
    assert job.next_poll_at == 0
    assert job.poll_errors == 0
    assert job.missing_polls == 0


def test_retry_after_partial_handoff(env):
    o, _, adm = env
    j = Job(source="m", folder="C:/dl", state=READY, torrent_id=7,
            files=[{"id": 1, "name": "a.mkv"}, {"id": 2, "name": "b.mkv"}], handed=1)
    o.jobs[j.job_id] = j
    o.tick()
    assert j.state == DONE
    assert [h[2] for h in adm.handed] == ["b.mkv"]


def test_match_returns_none_for_unknown_source(env):
    o, _, _ = env
    assert o.match("magnet:?xt=urn:btih:new") is None


def test_resume_or_submit_resumes_pending(env):
    o, _, _ = env
    o.submit("C:/dl", magnet="magnet:?xt=urn:btih:x")
    j = o.resume_or_submit("magnet:?xt=urn:btih:x", "C:/dl")
    assert j.state == CLOUD_PENDING
    assert len(o.jobs) == 1  # resumed, not re-added


def test_resume_or_submit_retries_failed_handoff_with_files(env):
    o, _, _ = env
    j = Job(source="magnet:?xt=urn:btih:x", folder="C:/dl", state=FAILED, torrent_id=7,
            files=[{"id": 1, "name": "a.mkv"}], error="abdm down")
    o.jobs[j.job_id] = j
    got = o.resume_or_submit("magnet:?xt=urn:btih:x", "C:/dl")
    assert got is j
    assert got.state == READY
    assert got.error is None
    assert len(o.jobs) == 1


def test_resume_or_submit_retries_failed_handoff_without_files(env):
    o, _, _ = env
    j = Job(source="magnet:?xt=urn:btih:x", folder="C:/dl", state=FAILED, torrent_id=7,
            next_poll_at=50, poll_errors=2, missing_polls=1)
    o.jobs[j.job_id] = j
    got = o.resume_or_submit("magnet:?xt=urn:btih:x", "C:/dl")
    assert got.state == CLOUD_PENDING
    assert got.next_poll_at == 0
    assert got.poll_errors == 0
    assert got.missing_polls == 0


def test_resume_or_submit_done_job_is_returned_not_readded(env):
    o, tb, _ = env
    j = Job(source="magnet:?xt=urn:btih:x", folder="C:/dl", state=DONE, torrent_id=7)
    o.jobs[j.job_id] = j
    got = o.resume_or_submit("magnet:?xt=urn:btih:x", "C:/dl")
    assert got is j
    assert len(o.jobs) == 1
    assert tb.created is None


def test_submit_ambiguous_create_adopts_cloud_item(env):
    o, tb, _ = env
    tb.create_raises = True
    tb.items = [{"hash": "ABC", "id": 9}]
    j = o.submit("C:/dl", magnet="magnet:?xt=urn:btih:abc&dn=x")
    assert j.state == CLOUD_PENDING
    assert j.torrent_id == 9


def test_submit_ambiguous_create_no_match_fails(env):
    o, tb, _ = env
    tb.create_raises = True
    tb.items = []
    j = o.submit("C:/dl", magnet="magnet:?xt=urn:btih:abc&dn=x")
    assert j.state == FAILED


def test_btih_without_query_returns_none():
    assert orch._btih("magnet:urn:btih:abc") is None


def test_submit_ambiguous_malformed_magnet_fails_without_crash(env):
    o, tb, _ = env
    tb.create_raises = True
    tb.items = [{"hash": "abc", "id": 9}]
    j = o.submit("C:/dl", magnet="magnet:urn:btih:abc")
    assert j.state == FAILED  # no btih to reconcile on, so plain failure


def test_resume_failed_without_torrent_id_reconciles_before_readd(env):
    o, tb, _ = env
    j = Job(source="magnet:?xt=urn:btih:abc", folder="C:/dl", state=FAILED, error="x")
    o.jobs[j.job_id] = j
    tb.items = [{"hash": "abc", "id": 9}]
    got = o.resume_or_submit("magnet:?xt=urn:btih:abc", "C:/dl")
    assert got is j
    assert got.state == CLOUD_PENDING
    assert got.torrent_id == 9
    assert tb.created is None


def test_resume_or_submit_torrent_path(env, tmp_path):
    o, tb, _ = env
    p = tmp_path / "x.torrent"
    p.write_bytes(b"d8:announce0:e")
    j = o.resume_or_submit(str(p), "C:/dl")
    assert tb.created == str(p)
    assert j.state == CLOUD_PENDING


def test_abdm_down_fails_then_retry_hands_all(env):
    o, _, adm = env
    adm.fail_times = 1
    j = Job(source="m", folder="C:/dl", state=READY, torrent_id=7,
            files=[{"id": 1, "name": "a.mkv"}, {"id": 2, "name": "b.mkv"}])
    o.jobs[j.job_id] = j
    o.tick()
    assert j.state == FAILED
    assert j.handed == 0
    j.state = READY
    o.tick()
    assert j.state == READY
    o.tick()
    assert j.state == DONE
    assert len(adm.handed) == 2


def test_save_failure_leaves_previous_file_intact(env, monkeypatch):
    o, _, _ = env
    o.submit("C:/dl", magnet="m")
    o.save()
    before = orch.JOBS_FILE.read_text()

    def boom(*a, **k):
        raise ValueError("bad json")

    monkeypatch.setattr(orch.json, "dumps", boom)
    with pytest.raises(ValueError):
        o.save()
    assert orch.JOBS_FILE.read_text() == before


def test_handed_persists_per_file(monkeypatch, tmp_path):
    monkeypatch.setattr(orch, "JOBS_FILE", tmp_path / "jobs.json")
    tb = _StubTB()
    o = Orchestrator(tb=tb, adm=_FailSecond())
    j = Job(source="m", folder="C:/dl", state=READY, torrent_id=7,
            files=[{"id": 1, "name": "a.mkv"}, {"id": 2, "name": "b.mkv"}])
    o.jobs[j.job_id] = j
    o.tick()
    assert j.state == READY
    assert j.handed == 1
    o.tick()
    assert j.state == FAILED
    o2 = Orchestrator(tb=tb, adm=_StubAdm())
    assert o2.jobs[j.job_id].handed == 1  # the first handoff survived the reload


def test_save_prunes_old_finished_jobs_but_keeps_active_ones(env):
    o, _, _ = env
    done_ids = []
    for i in range(orch.KEEP_TERMINAL + 3):
        j = Job(source=f"magnet:{i}", folder="C:/dl", state=DONE)
        o.jobs[j.job_id] = j
        done_ids.append(j.job_id)
    active = Job(source="magnet:running", folder="C:/dl", state=RECEIVED)
    o.jobs[active.job_id] = active

    o.save()

    assert len(o.jobs) == orch.KEEP_TERMINAL + 1
    assert active.job_id in o.jobs
    # insertion order is the only recency signal; the oldest go first
    for jid in done_ids[:3]:
        assert jid not in o.jobs
    for jid in done_ids[3:]:
        assert jid in o.jobs
