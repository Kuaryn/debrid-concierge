import os

import pytest
import requests

from concierge.providers import torbox

HASH = "5b1e0d988fc7a0c9e99bd852071681a59974b39f"

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def client():
    key = os.environ.get("DEBRID_CONCIERGE_TEST_KEY")
    if not key:
        pytest.skip("DEBRID_CONCIERGE_TEST_KEY is not set")
    return torbox.TorBoxClient(key)


def test_user_me_has_plan(client):
    assert "plan" in client.user_me()


def test_checkcached_lists_files(client):
    res = client.checkcached([HASH])
    assert HASH in res
    assert res[HASH]["files"]


def test_mylist_returns_items(client):
    items = client.mylist()
    assert isinstance(items, list) and items


def test_requestdl_streams_first_byte(client):
    item = client.mylist()[0]
    url = client.requestdl(item["id"], item["files"][0]["id"])
    try:
        resp = requests.get(url, headers={"Range": "bytes=0-0"}, stream=True, timeout=15)
    except requests.RequestException as e:
        raise AssertionError(f"cdn range probe failed ({e.__class__.__name__})") from None
    try:
        assert resp.status_code in (200, 206)
    finally:
        resp.close()
