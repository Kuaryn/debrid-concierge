import json
import os

import pytest

from concierge import abdm, torrent
from concierge import orchestrator as orch
from concierge.orchestrator import CLOUD_PENDING, DONE, FAILED, READY, RECEIVED, Job, Orchestrator
from concierge.providers.torbox import CooldownLimit, TorBoxError

BTIH = "0123456789abcdef0123456789abcdef01234567"
BTIH_BASE32 = "AERUKZ4JVPG66AJDIVTYTK6N54ASGRLH"
TORRENT = b"d4:infod4:name1:xee"


class _StubTB:
    def __init__(self):
        self.created = None
        self.items = []
        self.create_raises = False
        self.list_error = False
        self.list_calls = 0
        self.create_calls = 0

    def create(self, magnet=None, torrent_path=None, **kw):
        self.create_calls += 1
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


def test_poll_saves_only_needed_file_fields_and_resumes(env, tmp_path):
    o, tb, adm = env
    folder = str(tmp_path / "downloads")
    j = o.submit(folder, magnet="m")
    tb.items = {"progress": 1, "files": [
        {"id": 1, "name": "pack/a.mkv", "size": 123, "s3_path": "unused"},
        {"id": 2, "mimetype": "application/octet-stream"},
    ]}
    expected = [{"id": 1, "name": "pack/a.mkv"}, {"id": 2, "name": None}]

    o.tick()

    assert json.loads(orch.JOBS_FILE.read_text())[0]["files"] == expected
    assert tb.items["files"][0]["s3_path"] == "unused"
    loaded = Orchestrator(tb=tb, adm=adm)
    loaded.tick()
    assert loaded.jobs[j.job_id].handed == 1
    loaded = Orchestrator(tb=tb, adm=adm)
    loaded.tick()
    assert adm.handed == [
        ("https://cdn.example/1", folder, "a.mkv"),
        ("https://cdn.example/2", folder, None),
    ]
    loaded = Orchestrator(tb=tb, adm=adm)
    done = loaded.match("m")
    assert loaded.load_warning is None
    assert done.state == DONE
    assert done.handed == 2
    assert done.files == expected
    assert tb.create_calls == 1


def test_large_file_list_stays_small_on_disk(env, tmp_path):
    o, tb, _ = env
    o.submit(str(tmp_path / "downloads"), magnet="m")
    tb.items = {"progress": 1, "files": [
        {"id": i, "name": f"pack/file-{i:04}.mkv", "size": 123456789,
         "md5": "a" * 32, "s3_path": "unused/" + "x" * 256,
         "mimetype": "video/x-matroska", "short_name": f"file-{i:04}.mkv"}
        for i in range(1, 1201)
    ]}

    o.tick()

    assert orch.JOBS_FILE.stat().st_size < 100_000
    loaded = Orchestrator(tb=tb, adm=_StubAdm())
    (job,) = loaded.jobs.values()
    assert loaded.load_warning is None
    assert job.state == READY
    assert len(job.files) == 1200
    assert job.files[-1] == {"id": 1200, "name": "pack/file-1200.mkv"}


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
    assert job.infohash is None


def test_broken_jobs_file_is_preserved_and_repaired(env):
    _, tb, adm = env
    orch.JOBS_FILE.write_text("{")

    loaded = Orchestrator(tb=tb, adm=adm)

    assert loaded.jobs == {}
    assert loaded.load_warning == "Some saved jobs were invalid. The original is in jobs.json.bad."
    assert orch.JOBS_FILE.read_text() == "[]"
    assert orch.JOBS_FILE.with_name("jobs.json.bad").read_text() == "{"


def test_jobs_root_must_be_a_list(env):
    _, tb, adm = env
    orch.JOBS_FILE.write_text('{}')

    loaded = Orchestrator(tb=tb, adm=adm)

    assert loaded.jobs == {}
    assert loaded.load_warning is not None


def test_mixed_jobs_keep_valid_records(env):
    _, tb, adm = env
    valid = {"source": "m", "folder": "C:/dl", "state": CLOUD_PENDING,
             "torrent_id": 7, "job_id": "kept"}
    original = json.dumps([valid, 4, {"source": "missing folder"},
                           {**valid, "job_id": "unknown", "extra": True},
                           {**valid, "job_id": "bad type", "polls": "1"}])
    orch.JOBS_FILE.write_text(original)

    loaded = Orchestrator(tb=tb, adm=adm)

    assert list(loaded.jobs) == ["kept"]
    assert json.loads(orch.JOBS_FILE.read_text())[0]["job_id"] == "kept"
    assert orch.JOBS_FILE.with_name("jobs.json.bad").read_text() == original


def test_jobs_backup_is_not_overwritten(env):
    _, tb, adm = env
    orch.JOBS_FILE.write_text("[]x")
    orch.JOBS_FILE.with_name("jobs.json.bad").write_text("older")

    loaded = Orchestrator(tb=tb, adm=adm)

    assert loaded.load_warning.endswith("jobs.json.bad.1.")
    assert orch.JOBS_FILE.with_name("jobs.json.bad").read_text() == "older"
    assert orch.JOBS_FILE.with_name("jobs.json.bad.1").read_text() == "[]x"


def test_bad_state_type_is_rejected(env):
    _, tb, adm = env
    orch.JOBS_FILE.write_text(json.dumps([{"source": "m", "folder": "C:/dl", "state": []}]))

    loaded = Orchestrator(tb=tb, adm=adm)

    assert loaded.jobs == {}
    assert loaded.load_warning is not None


def test_saved_infohash_is_normalized(env):
    _, tb, adm = env
    value = {"source": "x.torrent", "folder": "C:/dl", "state": CLOUD_PENDING,
             "torrent_id": 7, "infohash": BTIH.upper()}
    orch.JOBS_FILE.write_text(json.dumps([value]))

    loaded = Orchestrator(tb=tb, adm=adm)
    (job,) = loaded.jobs.values()
    assert job.infohash == BTIH


def test_invalid_saved_infohash_is_rejected(env):
    _, tb, adm = env
    value = {"source": "x.torrent", "folder": "C:/dl", "state": CLOUD_PENDING,
             "torrent_id": 7, "infohash": "not-a-hash"}
    orch.JOBS_FILE.write_text(json.dumps([value]))

    loaded = Orchestrator(tb=tb, adm=adm)
    assert loaded.jobs == {}
    assert loaded.load_warning is not None


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


def test_match_uses_hash_across_reordered_parameters(env):
    o, _, _ = env
    original = f"magnet:?xt=urn:btih:{BTIH}&dn=first&tr=https%3A%2F%2Fone.example"
    j = o.submit("C:/dl", magnet=original)

    changed = f"magnet:?tr=https%3A%2F%2Ftwo.example&dn=second&xt=urn:btih:{BTIH}"
    assert o.resume_or_submit(changed, "C:/dl") is j
    assert len(o.jobs) == 1


def test_match_treats_base32_and_hex_as_the_same_hash(env):
    o, _, _ = env
    j = o.submit("C:/dl", magnet=f"magnet:?xt=urn:btih:{BTIH}")

    assert o.match(f"magnet:?xt=urn:btih:{BTIH_BASE32}") is j


def test_match_keeps_distinct_hashes_separate(env):
    o, _, _ = env
    o.submit("C:/dl", magnet=f"magnet:?xt=urn:btih:{BTIH}&dn=same")
    other = "1123456789abcdef0123456789abcdef01234567"

    assert o.match(f"magnet:?xt=urn:btih:{other}&dn=same") is None


def test_match_falls_back_to_exact_malformed_source(env):
    o, _, _ = env
    source = "magnet:?xt=urn:btih:not-a-hash&dn=old"
    j = o.submit("C:/dl", magnet=source)

    assert o.match(source) is j
    assert o.match("magnet:?dn=old&xt=urn:btih:not-a-hash") is None


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
    tb.items = [{"hash": BTIH.upper(), "id": 9}]
    j = o.submit("C:/dl", magnet=f"magnet:?xt=urn:btih:{BTIH}&dn=x")
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


def test_btih_normalizes_hex_base32_and_encoded_xt():
    assert orch._btih(f"magnet:?xt=urn:btih:{BTIH.upper()}") == BTIH
    assert orch._btih(f"magnet:?xt=urn:btih:{BTIH_BASE32}") == BTIH
    encoded = f"magnet:?dn=x&xt=urn%3Abtih%3A{BTIH}"
    assert orch._btih(encoded) == BTIH


def test_btih_skips_unrelated_and_invalid_xt_values():
    magnet = f"magnet:?xt=urn:sha1:nope&xt=urn:btih:short&xt=urn:btih:{BTIH}"
    assert orch._btih(magnet) == BTIH
    assert orch._btih("magnet:?xt=urn:btih:not-a-hash") is None


def test_submit_ambiguous_malformed_magnet_fails_without_crash(env):
    o, tb, _ = env
    tb.create_raises = True
    tb.items = [{"hash": "abc", "id": 9}]
    j = o.submit("C:/dl", magnet="magnet:urn:btih:abc")
    assert j.state == FAILED  # no btih to reconcile on, so plain failure


def test_resume_failed_without_torrent_id_reconciles_before_readd(env):
    o, tb, _ = env
    source = f"magnet:?xt=urn:btih:{BTIH}"
    j = Job(source=source, folder="C:/dl", state=FAILED, error="x")
    o.jobs[j.job_id] = j
    tb.items = [{"hash": BTIH, "id": 9}]
    got = o.resume_or_submit(source, "C:/dl")
    assert got is j
    assert got.state == CLOUD_PENDING
    assert got.torrent_id == 9
    assert tb.created is None


def test_resume_or_submit_torrent_path(env, tmp_path):
    o, tb, _ = env
    p = tmp_path / "x.torrent"
    p.write_bytes(TORRENT)
    j = o.resume_or_submit(str(p), "C:/dl")
    assert tb.created == str(p)
    assert j.state == CLOUD_PENDING


def test_torrent_copy_resumes_by_infohash(env, tmp_path):
    o, tb, _ = env
    first = tmp_path / "first.torrent"
    second = tmp_path / "renamed.torrent"
    first.write_bytes(TORRENT)
    second.write_bytes(TORRENT)

    j = o.resume_or_submit(str(first), "C:/dl")
    assert o.resume_or_submit(str(second), "C:/dl") is j
    assert tb.create_calls == 1
    assert len(o.jobs) == 1


def test_torrent_metadata_outside_info_still_resumes(env, tmp_path):
    o, tb, _ = env
    first = tmp_path / "first.torrent"
    second = tmp_path / "second.torrent"
    first.write_bytes(b"d8:announce3:one4:infod4:name1:xee")
    second.write_bytes(b"d7:comment5:hello4:infod4:name1:xee")

    j = o.resume_or_submit(str(first), "C:/dl")
    assert o.resume_or_submit(str(second), "C:/dl") is j
    assert tb.create_calls == 1


def test_different_torrent_info_creates_another_job(env, tmp_path):
    o, tb, _ = env
    first = tmp_path / "first.torrent"
    second = tmp_path / "second.torrent"
    first.write_bytes(TORRENT)
    second.write_bytes(b"d4:infod4:name1:yee")

    o.resume_or_submit(str(first), "C:/dl")
    o.resume_or_submit(str(second), "C:/dl")
    assert tb.create_calls == 2
    assert len(o.jobs) == 2


def test_bad_torrent_fails_before_cloud_create(env, tmp_path):
    o, tb, _ = env
    path = tmp_path / "bad.torrent"
    path.write_bytes(b"not bencode")

    j = o.resume_or_submit(str(path), "C:/dl")
    assert j.state == FAILED
    assert j.error == "invalid torrent file"
    assert tb.create_calls == 0


def test_missing_torrent_fails_before_cloud_create(env, tmp_path):
    o, tb, _ = env
    j = o.resume_or_submit(str(tmp_path / "gone.torrent"), "C:/dl")
    assert j.state == FAILED
    assert j.error == "cannot read torrent file (FileNotFoundError)"
    assert tb.create_calls == 0


def test_ambiguous_torrent_upload_adopts_cloud_item(env, tmp_path):
    o, tb, _ = env
    path = tmp_path / "x.torrent"
    path.write_bytes(TORRENT)
    expected = torrent.infohash(TORRENT)
    tb.create_raises = True
    tb.items = [{"hash": expected, "id": 9}]

    j = o.resume_or_submit(str(path), "C:/dl")
    assert j.state == CLOUD_PENDING
    assert j.torrent_id == 9
    assert tb.create_calls == 1


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


def test_save_retries_a_temporary_replace_failure(env, monkeypatch):
    o, _, _ = env
    o.submit("C:/dl", magnet="m")
    real_replace = orch.os.replace
    waits = []

    def flaky_replace(source, target):
        if len(waits) < 2:
            raise PermissionError("file is open")
        real_replace(source, target)

    monkeypatch.setattr(orch.os, "replace", flaky_replace)
    monkeypatch.setattr(orch.time, "sleep", waits.append)

    o.save()

    assert waits == [orch.SAVE_RETRY_DELAY, orch.SAVE_RETRY_DELAY]
    assert not orch.JOBS_FILE.with_suffix(".tmp").exists()


def test_save_replace_failure_keeps_the_previous_file(env, monkeypatch):
    o, _, _ = env
    o.submit("C:/dl", magnet="m")
    before = orch.JOBS_FILE.read_text()
    o.jobs["new"] = Job(source="another", folder="C:/dl", state=DONE, job_id="new")

    def blocked_replace(source, target):
        raise PermissionError("file is open")

    monkeypatch.setattr(orch.os, "replace", blocked_replace)
    monkeypatch.setattr(orch.time, "sleep", lambda seconds: None)

    with pytest.raises(
            orch.JobsError, match=r"saved jobs could not be written \(PermissionError\)"):
        o.save()

    assert orch.JOBS_FILE.read_text() == before
    assert not orch.JOBS_FILE.with_suffix(".tmp").exists()


def test_save_write_failure_is_a_jobs_error(env, monkeypatch):
    o, _, _ = env
    o.submit("C:/dl", magnet="m")
    before = orch.JOBS_FILE.read_text()
    tmp = orch.JOBS_FILE.with_suffix(".tmp")
    real_write = type(tmp).write_text

    def fail_tmp(path, text, *args, **kwargs):
        if path == tmp:
            raise OSError("disk full")
        return real_write(path, text, *args, **kwargs)

    monkeypatch.setattr(type(tmp), "write_text", fail_tmp)

    with pytest.raises(orch.JobsError, match=r"saved jobs could not be written \(OSError\)"):
        o.save()

    assert orch.JOBS_FILE.read_text() == before
    assert not tmp.exists()


@pytest.mark.skipif(os.name != "nt", reason="windows file sharing")
def test_save_waits_for_an_open_reader(env, monkeypatch):
    o, _, _ = env
    o.submit("C:/dl", magnet="m")
    held = orch.JOBS_FILE.open()
    released = []

    def release_reader(seconds):
        held.close()
        released.append(seconds)

    monkeypatch.setattr(orch.time, "sleep", release_reader)
    try:
        o.save()
    finally:
        held.close()

    assert released == [orch.SAVE_RETRY_DELAY]


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
