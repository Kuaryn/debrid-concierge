import pytest
import requests

from concierge.providers import torbox


class _StubResp:
    status_code = 200

    def json(self):
        return {"success": True, "data": "ok"}


def _capture(client):
    seen = {}

    def fake(method, url, **kw):
        seen.update(kw)
        seen["method"] = method
        seen["url"] = url
        return _StubResp()

    client.http.request = fake
    return seen


def test_control_sends_json_body():
    c = torbox.TorBoxClient("dummy")
    seen = _capture(c)
    c.control(5, "pause")
    assert seen["json"] == {"torrent_id": 5, "operation": "pause"}


def test_create_magnet_sends_form_data():
    c = torbox.TorBoxClient("dummy")
    seen = _capture(c)
    c.create(magnet="magnet:?xt=urn:btih:x")
    assert seen["data"]["magnet"] == "magnet:?xt=urn:btih:x"


def test_requestdl_passes_token_in_params():
    c = torbox.TorBoxClient("dummy")
    seen = _capture(c)
    url = c.requestdl(1, 0)
    assert seen["params"]["token"] == "dummy"
    assert "token" not in seen["url"]
    assert url == "ok"


def test_create_does_not_retry_network_failure():
    # a timed-out add may have landed server-side; retrying would duplicate it
    c = torbox.TorBoxClient("dummy")
    calls = []

    def fake(method, url, **kw):
        calls.append(1)
        raise requests.ConnectionError("boom")

    c.http.request = fake
    with pytest.raises(torbox.TorBoxError):
        c.create(magnet="magnet:?xt=urn:btih:x")
    assert len(calls) == 1


def test_magnettofile_returns_raw_bytes():
    c = torbox.TorBoxClient("dummy")

    class _Raw:
        status_code = 200
        content = b"d8:announce0:e"

    c.http.request = lambda method, url, **kw: _Raw()
    assert c.magnettofile("magnet:?xt=urn:btih:x") == b"d8:announce0:e"


def test_create_torrent_file_sends_multipart(tmp_path):
    c = torbox.TorBoxClient("dummy")
    seen = _capture(c)
    p = tmp_path / "x.torrent"
    p.write_bytes(b"d8:announce0:e")
    c.create(torrent_path=str(p))
    assert seen["files"]["file"].name == str(p)
