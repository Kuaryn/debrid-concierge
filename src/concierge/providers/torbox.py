"""TorBox API client covering the endpoints the concierge actually uses."""

import re
import time
from collections import deque

import requests

BASE = "https://api.torbox.app/v1/api"


class TorBoxError(Exception):
    pass


class CooldownLimit(TorBoxError):
    def __init__(self, detail: str, cooldown_until):
        super().__init__(detail)
        self.detail = detail
        self.cooldown_until = cooldown_until


class ActiveLimit(TorBoxError):
    pass


class MonthlyLimit(TorBoxError):
    pass


def _raise_for_error(payload: dict) -> None:
    # error shape: {"success": false, "error": "...", "detail": "...", "data": {...}}
    name = payload.get("error") or ""
    detail = payload.get("detail") or name or "unknown torbox error"
    if name == "COOLDOWN_LIMIT":
        data = payload.get("data") or {}
        raise CooldownLimit(detail, data.get("cooldown_until"))
    cls = {"ACTIVE_LIMIT": ActiveLimit, "MONTHLY_LIMIT": MonthlyLimit}.get(name)
    if cls:
        raise cls(detail)
    raise TorBoxError(detail)


def _parse(resp: requests.Response):
    if resp.status_code == 401:
        raise TorBoxError("torbox rejected the api key (401)")
    try:
        payload = resp.json()
    except ValueError:
        raise TorBoxError(f"non-json response (http {resp.status_code})")
    if payload.get("success") is False:
        _raise_for_error(payload)
    return payload.get("data", payload)


def _safe_message(err: Exception) -> str:
    # requests error text embeds the raw url; requestdl urls carry the key
    return re.sub(r"token=[^&\s)]+", "token=<redacted>", str(err))


MAX_ADDS_PER_HOUR = 55  # torbox caps uncached adds at 60/hour, leave headroom


class TorBoxClient:
    def __init__(self, api_key: str):
        self.key = api_key
        self.http = requests.Session()
        self.http.headers["Authorization"] = f"Bearer {api_key}"
        self._add_times = deque()

    def _request(self, method: str, path: str, *, params=None, data=None, files=None, json=None):
        last_err = None
        for attempt in range(3):
            if attempt:
                time.sleep(2 ** (attempt - 1))
            try:
                if files:
                    for fh in files.values():
                        fh.seek(0)  # multipart handles need rewinding between tries
                resp = self.http.request(
                    method, BASE + "/" + path,
                    params=params, data=data, files=files, json=json, timeout=15,
                )
                if resp.status_code < 500:
                    return _parse(resp)
                last_err = TorBoxError(f"torbox http {resp.status_code}")
            except requests.RequestException as e:
                last_err = e
        raise TorBoxError(f"torbox unreachable after 3 tries ({_safe_message(last_err)})")

    def _budget_ok(self) -> bool:
        now = time.monotonic()
        while self._add_times and now - self._add_times[0] > 3600:
            self._add_times.popleft()
        return len(self._add_times) < MAX_ADDS_PER_HOUR

    def user_me(self) -> dict:
        return self._request("GET", "user/me", params={"settings": "false"})

    def checkcached(self, hashes: list, list_files: bool = True) -> dict:
        params = {"hash": ",".join(hashes), "format": "object", "bypass_cache": "true"}
        if list_files:
            params["list_files"] = "true"
        return self._request("GET", "torrents/checkcached", params=params)

    def mylist(self, torrent_id: int | None = None) -> list:
        params = {"bypass_cache": "true"}
        if torrent_id:
            params["id"] = str(torrent_id)
        return self._request("GET", "torrents/mylist", params=params)

    def requestdl(self, torrent_id: int, file_id: int, zip_link: bool = False) -> str:
        # torbox puts the raw key in the query string here; never log this url
        params = {"token": self.key, "torrent_id": str(torrent_id), "file_id": str(file_id)}
        if zip_link:
            params["zip_link"] = "true"
        return self._request("GET", "torrents/requestdl", params=params)

    def create(self, magnet: str | None = None, torrent_path: str | None = None,
               seed: int = 3, as_queued: bool = False,
               add_only_if_cached: bool = False) -> dict:
        if not self._budget_ok():
            raise TorBoxError("add budget spent (55 adds in the past hour)")
        # count attempts, not successes: a timed-out add may still land server-side
        self._add_times.append(time.monotonic())
        form = {"seed": str(seed)}
        if as_queued:
            form["as_queued"] = "true"
        if add_only_if_cached:
            form["add_only_if_cached"] = "true"
        if magnet:
            form["magnet"] = magnet
            return self._request("POST", "torrents/createtorrent", data=form)
        with open(torrent_path, "rb") as fh:
            return self._request("POST", "torrents/createtorrent", data=form, files={"file": fh})

    def control(self, torrent_id: int, operation: str) -> dict:
        # deleting triggers a ~24h account cooldown, hence the warning
        return self._request(
            "POST", "torrents/controltorrent",
            json={"torrent_id": torrent_id, "operation": operation},
        )
