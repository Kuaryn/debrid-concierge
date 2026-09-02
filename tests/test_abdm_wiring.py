import pytest
import requests

from concierge import abdm


class _StubResp:
    def __init__(self, status_code=200):
        self.status_code = status_code


def _capture(monkeypatch, status=200):
    seen = {}

    def fake(url, **kw):
        seen["url"] = url
        seen.update(kw)
        return _StubResp(status)

    monkeypatch.setattr(abdm.requests, "post", fake)
    return seen


def test_handoff_sends_startdownload_true(monkeypatch):
    seen = _capture(monkeypatch)
    c = abdm.AbdmClient(key="dummy", port=15151)
    c.handoff("https://cdn.example/file.mkv", "C:/Downloads/ABDM/TV", name="file.mkv")
    assert seen["url"] == "http://localhost:15151/start-headless-download"
    assert seen["headers"] == {"X-Api-Key": "dummy"}
    body = seen["json"]
    assert body["startDownload"] is True
    assert body["folder"] == "C:/Downloads/ABDM/TV"
    assert body["name"] == "file.mkv"
    assert body["downloadSource"] == {"type": "http", "link": "https://cdn.example/file.mkv"}


def test_explicit_connection_skips_settings(monkeypatch):
    def fail():
        raise AssertionError("settings must not be read")

    monkeypatch.setattr(abdm, "_read_settings", fail)
    c = abdm.AbdmClient(key="dummy", port=15151)
    assert c.key == "dummy"
    assert c.base == "http://localhost:15151"


def test_ping_posts_to_ping(monkeypatch):
    seen = _capture(monkeypatch)
    c = abdm.AbdmClient(key="dummy")
    assert c.ping() is True
    assert seen["url"].endswith("/ping")


def test_401_raises_auth(monkeypatch):
    _capture(monkeypatch, status=401)
    c = abdm.AbdmClient(key="bad")
    with pytest.raises(abdm.AbdmAuth):
        c.ping()


def test_refused_raises_down(monkeypatch):
    def fake(url, **kw):
        raise requests.ConnectionError("refused")

    monkeypatch.setattr(abdm.requests, "post", fake)
    c = abdm.AbdmClient(key="dummy")
    with pytest.raises(abdm.AbdmDown):
        c.ping()


def test_missing_settings_yields_no_key(monkeypatch, tmp_path):
    monkeypatch.setattr(abdm, "SETTINGS", tmp_path / "nope.json")
    c = abdm.AbdmClient()
    assert c.key is None
    assert c.base == "http://localhost:15151"
