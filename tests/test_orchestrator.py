import pytest

from concierge import abdm
from concierge import orchestrator as orch
from concierge.orchestrator import CLOUD_PENDING, DONE, FAILED, READY, Job, Orchestrator
from concierge.providers.torbox import CooldownLimit, TorBoxError


class _StubTB:
    def __init__(self):
        self.created = None
        self.items = []
        self.create_raises = False

    def create(self, magnet=None, torrent_path=None, **kw):
        if self.create_raises:
            raise TorBoxError("torbox unreachable after 1 tries (timeout)")
        self.created = magnet or torrent_path
        return {"torrent_id": 7}

    def mylist(self, torrent_id=None):
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


def test_poll_handles_dict_response(env):
    o, tb, _ = env
    j = o.submit("C:/dl", magnet="m")
    tb.items = {"progress": 1, "download_finished": True, "files": [{"id": 1, "name": "a.mkv"}]}
    o.tick()
    assert j.state == READY


def test_multifile_poll_then_handoff(env):
    o, tb, adm = env
    j = o.submit("C:/dl", magnet="m")
    tb.items = [{"progress": 1, "download_finished": True,
                 "files": [{"id": 1, "name": "a.mkv"}, {"id": 2, "name": "b.mkv"}]}]
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
    j = Job(source="magnet:?xt=urn:btih:x", folder="C:/dl", state=FAILED, torrent_id=7)
    o.jobs[j.job_id] = j
    got = o.resume_or_submit("magnet:?xt=urn:btih:x", "C:/dl")
    assert got.state == CLOUD_PENDING


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
    assert j.state == FAILED
    o2 = Orchestrator(tb=tb, adm=_StubAdm())
    assert o2.jobs[j.job_id].handed == 1  # the first handoff survived the reload
