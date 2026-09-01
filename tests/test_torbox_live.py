import pytest
import requests

from concierge import config
from concierge.providers import torbox

HASH = "5b1e0d988fc7a0c9e99bd852071681a59974b39f"

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def client():
    key = config.get_torbox_key()
    if key is None:
        pytest.skip("set key first: python src/concierge/config.py set-key")
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
    # url carries the api key in the query string; never print or log it
    resp = requests.get(url, headers={"Range": "bytes=0-0"}, stream=True, timeout=15)
    assert resp.status_code in (200, 206)
    resp.close()
