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
