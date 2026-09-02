"""AB Download Manager hand-off over its localhost REST API."""

import json
from pathlib import Path

import requests

SETTINGS = Path.home() / ".abdm" / "config" / "appSettings.json"


class AbdmError(Exception):
    pass


class AbdmDown(AbdmError):
    pass


class AbdmAuth(AbdmError):
    pass


def _read_settings() -> dict:
    try:
        return json.loads(SETTINGS.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}


class AbdmClient:
    def __init__(self, key: str | None = None, port: int | None = None):
        cfg = {} if key is not None and port is not None else _read_settings()
        self.key = key if key is not None else cfg.get("apiAuthKey")
        self.base = f"http://localhost:{port or cfg.get('apiPort') or 15151}"

    def _post(self, path: str, body: dict | None = None):
        try:
            resp = requests.post(
                self.base + path, json=body,
                headers={"X-Api-Key": self.key or ""}, timeout=10,
            )
        except requests.RequestException as e:
            raise AbdmDown(f"abdm unreachable ({e.__class__.__name__})")
        if resp.status_code == 401:
            raise AbdmAuth("abdm rejected the api key (401)")
        if resp.status_code >= 400:
            raise AbdmError(f"abdm http {resp.status_code}")
        return resp

    def ping(self) -> bool:
        return self._post("/ping").status_code == 200

    def handoff(self, link: str, folder: str, name: str | None = None,
                headers: dict | None = None) -> None:
        # abdm accepts C:/ and C:\ folder forms alike (probed live), pass through
        src = {"type": "http", "link": link}
        if headers:
            src["headers"] = headers
        body = {
            "downloadSource": src,
            "folder": folder,
            "name": name,
            "queueId": None,
            "categoryId": None,
            "startDownload": True,  # server default is false; omitting parks the task
            "startQueue": False,
        }
        self._post("/start-headless-download", body)
