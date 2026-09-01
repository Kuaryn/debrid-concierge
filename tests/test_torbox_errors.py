import json

import pytest
import requests

from concierge.providers import torbox


def _fake_response(status: int, body: dict):
    resp = requests.Response()
    resp.status_code = status
    resp.encoding = "utf-8"
    resp._content = json.dumps(body).encode("utf-8")
    return resp


def test_cooldown_limit_carries_until():
    payload = {
        "success": False,
        "error": "COOLDOWN_LIMIT",
        "detail": "you are on cooldown",
        "data": {"cooldown_until": "2026-09-01T06:45:40Z"},
    }
    with pytest.raises(torbox.CooldownLimit) as exc:
        torbox._raise_for_error(payload)
    assert exc.value.cooldown_until == "2026-09-01T06:45:40Z"
    assert "cooldown" in str(exc.value)


def test_active_limit():
    payload = {"success": False, "error": "ACTIVE_LIMIT", "detail": "slot limit hit"}
    with pytest.raises(torbox.ActiveLimit):
        torbox._raise_for_error(payload)


def test_monthly_limit():
    payload = {"success": False, "error": "MONTHLY_LIMIT", "detail": "bandwidth spent"}
    with pytest.raises(torbox.MonthlyLimit):
        torbox._raise_for_error(payload)


def test_unknown_error_falls_back():
    with pytest.raises(torbox.TorBoxError):
        torbox._raise_for_error({"success": False, "error": "MYSTERY", "detail": "boom"})


def test_error_without_detail_uses_name():
    with pytest.raises(torbox.TorBoxError, match="MYSTERY"):
        torbox._raise_for_error({"success": False, "error": "MYSTERY"})


def test_blank_error_uses_generic_message():
    with pytest.raises(torbox.TorBoxError, match="unknown torbox error"):
        torbox._raise_for_error({"success": False})


def test_parse_unwraps_data():
    resp = _fake_response(200, {"success": True, "data": {"plan": "essential"}})
    assert torbox._parse(resp) == {"plan": "essential"}


def test_parse_keeps_payload_without_data_key():
    resp = _fake_response(200, {"success": True})
    assert torbox._parse(resp) == {"success": True}


def test_parse_wraps_error_payloads():
    resp = _fake_response(200, {"success": False, "error": "ACTIVE_LIMIT", "detail": "slots"})
    with pytest.raises(torbox.ActiveLimit):
        torbox._parse(resp)


def test_parse_rejects_401():
    resp = _fake_response(401, {"detail": "unauthorized"})
    with pytest.raises(torbox.TorBoxError, match="api key"):
        torbox._parse(resp)


def test_safe_message_redacts_token():
    err = requests.RequestException(
        "Max retries exceeded with url: /v1/torrents/requestdl?token=SECRET123&torrent_id=1 (boom)"
    )
    out = torbox._safe_message(err)
    assert "SECRET123" not in out
    assert "token=<redacted>" in out
