"""Local stand-ins for TorBox and ABDM so the full pipeline runs against
real HTTP without touching the network. The TorBox fake is strict on
purpose: file parts without a content type get the same BOZO rejection the
real server gave us, so that regression can't quietly come back."""

import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from concierge import orchestrator as orch
from concierge.abdm import AbdmClient
from concierge.orchestrator import Orchestrator
from concierge.providers import torbox

BTIH = "0123456789abcdef0123456789abcdef01234567"
TORRENT = b"d4:infod4:name1:xee"


def _start(handler):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv


class _TorBoxHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        s = self.server.state
        path = urllib.parse.urlparse(self.path).path
        if path == "/v1/api/torrents/mylist":
            s["mylist_calls"] += 1
            if s["mylist_calls"] > s["finish_after_polls"]:
                for t in s["torrents"]:
                    t["download_finished"] = True
            return self._json({"success": True, "data": s["torrents"]})
        if path == "/v1/api/torrents/requestdl":
            # the key rides in the query string here; this is the url we
            # must never let into error text
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            assert qs["token"] == [self.server.api_key]
            link = f"https://dl.example/f{qs['file_id'][0]}.mp4?token={qs['token'][0]}"
            return self._json({"success": True, "data": link})
        return self._json({"success": False, "error": "no such endpoint"}, 404)

    def do_POST(self):
        s = self.server.state
        if self.path == "/v1/api/torrents/createtorrent":
            return self._create(s)
        return self._json({"success": False, "error": "no such endpoint"}, 404)

    def _create(self, s):
        s["creates"] += 1
        if s.get("fail_create_once"):
            s["fail_create_once"] = False
            return self._json({"success": False, "error": "server on fire"}, 500)
        ctype = self.headers.get("Content-Type", "")
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        if ctype.startswith("multipart/form-data"):
            magnet = None
            torrent_ok = False
            boundary = ctype.split("boundary=")[1].encode()
            for part in body.split(b"--" + boundary):
                if not part.strip() or part.strip() == b"--":
                    continue
                head, _, val = part.partition(b"\r\n\r\n")
                val = val.rstrip(b"\r\n")
                head = head.decode()
                if 'filename="' in head:
                    # real torbox rejects file parts with no content type;
                    # keep that behavior so the bug stays pinned
                    if "application/x-bittorrent" in head:
                        torrent_ok = True
                    else:
                        return self._json(
                            {"success": False, "error": "BOZO_TORRENT"}, 400)
                elif 'name="magnet"' in head:
                    magnet = val.decode()
            if not torrent_ok:
                return self._json({"success": False, "error": "no file part"}, 400)
        else:
            magnet = urllib.parse.parse_qs(body.decode()).get("magnet", [None])[0]
        s["next_id"] += 1
        h = magnet.split("urn:btih:")[1].split("&")[0] if magnet else f"hash{s['next_id']}"
        s["torrents"].append({
            "id": s["next_id"], "hash": h, "name": "thing",
            "download_finished": False, "progress": 0,
            "files": [{"id": 1, "name": "a.mkv"}],
        })
        return self._json({"success": True, "data": {"torrent_id": s["next_id"]}})


class _AbdmHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.server.calls.append({
            "key": self.headers.get("X-Api-Key"),
            "body": json.loads(self.rfile.read(length)),
        })
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"{}")


@pytest.fixture(autouse=True)
def _real_jobs_file_off(monkeypatch, tmp_path):
    # tick() saves; without this the tests would write into the real
    # %APPDATA% jobs.json and poison the tray. learned the hard way.
    monkeypatch.setattr(orch, "JOBS_FILE", tmp_path / "jobs.json")


@pytest.fixture
def torbox_fake(monkeypatch):
    srv = _start(_TorBoxHandler)
    srv.state = {
        "torrents": [], "creates": 0, "mylist_calls": 0,
        "next_id": 0, "finish_after_polls": 1,
    }
    srv.api_key = "testkey"
    monkeypatch.setattr(torbox, "BASE", f"http://127.0.0.1:{srv.server_address[1]}/v1/api")
    yield srv
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def abdm_fake():
    srv = _start(_AbdmHandler)
    srv.calls = []
    yield srv
    srv.shutdown()
    srv.server_close()


def _orch(torbox_fake, abdm_fake):
    tb = torbox.TorBoxClient(torbox_fake.api_key)
    port = abdm_fake.server_address[1]
    return Orchestrator(tb=tb, adm=AbdmClient(key="abdmkey", port=port))


def test_magnet_runs_all_the_way_to_done(torbox_fake, abdm_fake):
    o = _orch(torbox_fake, abdm_fake)
    j = o.submit("C:/dl", magnet="magnet:?xt=urn:btih:abc&dn=thing")
    assert j.state == orch.CLOUD_PENDING
    o.tick()  # first poll: still downloading
    assert j.state == orch.CLOUD_PENDING
    o.tick()  # download finished, links fetched
    assert j.state == orch.READY
    o.tick()  # hand it to abdm
    assert j.state == orch.DONE
    assert torbox_fake.state["creates"] == 1
    assert len(abdm_fake.calls) == 1
    call = abdm_fake.calls[0]
    assert call["key"] == "abdmkey"
    assert call["body"]["startDownload"] is True
    assert call["body"]["folder"] == "C:/dl"
    assert call["body"]["name"] == "a.mkv"
    # the download link torbox handed out carries the key in the query
    assert "token=testkey" in call["body"]["downloadSource"]["link"]


def test_failed_create_adopts_cloud_copy_by_hash(torbox_fake, abdm_fake):
    torbox_fake.state["fail_create_once"] = True
    # pretend the "failed" add actually landed server-side
    torbox_fake.state["torrents"].append({
        "id": 99, "hash": BTIH, "name": "thing",
        "download_finished": True, "progress": 1,
        "files": [{"id": 1, "name": "a.mkv"}],
    })
    o = _orch(torbox_fake, abdm_fake)
    j = o.submit("C:/dl", magnet=f"magnet:?xt=urn:btih:{BTIH}&dn=thing")
    # adopted the cloud copy instead of adding a second one
    assert j.torrent_id == 99
    assert torbox_fake.state["creates"] == 1
    o.tick()
    assert j.state == orch.READY
    o.tick()
    assert j.state == orch.DONE


def test_torrent_upload_pins_the_file_content_type(torbox_fake, abdm_fake, tmp_path):
    p = tmp_path / "x.torrent"
    p.write_bytes(TORRENT)
    o = _orch(torbox_fake, abdm_fake)
    j = o.submit("C:/dl", torrent_path=str(p))
    assert j.state == orch.CLOUD_PENDING
    # got past the strict multipart check, so the part carried the type
    assert torbox_fake.state["creates"] == 1
