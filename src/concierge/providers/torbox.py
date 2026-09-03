"""TorBox API client covering the endpoints the concierge actually uses."""

import ipaddress
import re
import time
from collections import deque
from pathlib import Path
from urllib.parse import urlsplit

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


def _safe_message(err) -> str:
    return re.sub(r"token(?:=|%3d)[^&\s)]+", "token=<redacted>", str(err),
                  flags=re.IGNORECASE)


def _raise_for_error(payload: dict) -> None:
    # error shape: {"success": false, "error": "...", "detail": "...", "data": {...}}
    name = payload.get("error")
    name = name if isinstance(name, str) else ""
    detail = _safe_message(payload.get("detail") or name or "unknown torbox error")
    if name == "COOLDOWN_LIMIT":
        data = payload.get("data") or {}
        until = data.get("cooldown_until") if isinstance(data, dict) else None
        raise CooldownLimit(detail, until)
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
    if not isinstance(payload, dict):
        raise TorBoxError("torbox returned an invalid response")
    if payload.get("success") is False:
        _raise_for_error(payload)
    if not 200 <= resp.status_code < 300:
        detail = payload.get("detail") or payload.get("error") or f"http {resp.status_code}"
        raise TorBoxError(_safe_message(detail))
    return payload.get("data", payload)


def _download_url(value) -> str:
    if not isinstance(value, str) or "\\" in value or any(c.isspace() for c in value):
        raise TorBoxError("torbox returned an invalid download url")
    try:
        parts = urlsplit(value)
    except ValueError:
        raise TorBoxError("torbox returned an invalid download url") from None
    host = parts.hostname
    if (parts.scheme != "https" or not host or parts.username is not None
            or parts.password is not None):
        raise TorBoxError("torbox returned an invalid download url")
    clean_host = host.rstrip(".").lower()
    if clean_host == "localhost" or clean_host.endswith((".localhost", ".local")):
        raise TorBoxError("torbox returned a local download url")
    try:
        address = ipaddress.ip_address(clean_host)
    except ValueError:
        if "." not in clean_host:
            raise TorBoxError("torbox returned an invalid download url") from None
        return value
    if not address.is_global:
        raise TorBoxError("torbox returned a local download url")
    return value


MAX_ADDS_PER_HOUR = 55  # torbox caps uncached adds at 60/hour, leave headroom


class TorBoxClient:
    def __init__(self, api_key: str):
        self.key = api_key
        self.http = requests.Session()
        self.http.headers["Authorization"] = f"Bearer {api_key}"
        self._add_times = deque()

    def _request(self, method: str, path: str, *, params=None, data=None, files=None,
                 json=None, tries: int = 3):
        last_err = None
        for attempt in range(tries):
            if attempt:
                time.sleep(2 ** (attempt - 1))
            try:
                if files:
                    for v in files.values():
                        (v[1] if isinstance(v, tuple) else v).seek(0)  # rewind between tries
                resp = self.http.request(
                    method, BASE + "/" + path,
                    params=params, data=data, files=files, json=json, timeout=15,
                )
                if resp.status_code < 500:
                    return _parse(resp)
                last_err = TorBoxError(f"torbox http {resp.status_code}")
            except requests.RequestException as e:
                last_err = e
        raise TorBoxError(f"torbox unreachable after {tries} tries ({_safe_message(last_err)})")

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
        return _download_url(self._request("GET", "torrents/requestdl", params=params))

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
            # no retry: a timed-out add may still land; caller reconciles
            return self._request("POST", "torrents/createtorrent", data=form, tries=1)
        if not torrent_path:
            raise TorBoxError("torrent path is required")
        path = Path(torrent_path)
        try:
            with path.open("rb") as fh:
                # torbox's parser rejects file parts without a content type
                return self._request(
                    "POST", "torrents/createtorrent", data=form,
                    files={"file": (path.name, fh, "application/x-bittorrent")},
                    tries=1,
                )
        except OSError as e:
            raise TorBoxError(f"cannot read torrent file ({e.__class__.__name__})") from None

    def magnettofile(self, magnet: str) -> bytes:
        # conversion only, adds nothing to the cloud; raw bencode, not the
        # usual {success, data} envelope
        resp = self.http.request(
            "POST", BASE + "/torrents/magnettofile",
            json={"magnet": magnet}, timeout=15,
        )
        if resp.status_code != 200:
            raise TorBoxError(f"magnettofile http {resp.status_code}")
        return resp.content

    def control(self, torrent_id: int, operation: str) -> dict:
        # deleting triggers a ~24h account cooldown, hence the warning
        return self._request(
            "POST", "torrents/controltorrent",
            json={"torrent_id": torrent_id, "operation": operation},
        )
