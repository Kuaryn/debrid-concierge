import hashlib
from pathlib import Path

MAX_DEPTH = 100


class TorrentError(Exception):
    pass


def _string(data: bytes, pos: int) -> tuple[int, bytes]:
    colon = data.find(b":", pos)
    if colon < 0:
        raise ValueError
    raw = data[pos:colon]
    if not raw or not raw.isdigit() or len(raw) > 1 and raw.startswith(b"0"):
        raise ValueError
    end = colon + 1 + int(raw)
    if end > len(data):
        raise ValueError
    return end, data[colon + 1:end]


def _skip(data: bytes, pos: int, depth: int = 0) -> int:
    if pos >= len(data) or depth >= MAX_DEPTH:
        raise ValueError
    token = data[pos:pos + 1]
    if token.isdigit():
        return _string(data, pos)[0]
    if token == b"i":
        end = data.find(b"e", pos + 1)
        if end < 0:
            raise ValueError
        raw = data[pos + 1:end]
        digits = raw[1:] if raw.startswith(b"-") else raw
        if (not digits or not digits.isdigit()
                or len(digits) > 1 and digits.startswith(b"0")
                or raw == b"-0"):
            raise ValueError
        return end + 1
    if token not in (b"l", b"d"):
        raise ValueError
    pos += 1
    while pos < len(data) and data[pos:pos + 1] != b"e":
        if token == b"d":
            pos, _ = _string(data, pos)
        pos = _skip(data, pos, depth + 1)
    if pos >= len(data):
        raise ValueError
    return pos + 1


def infohash(data: bytes) -> str:
    if not isinstance(data, bytes) or not data.startswith(b"d"):
        raise TorrentError("invalid torrent file")
    pos = 1
    span = None
    try:
        while pos < len(data) and data[pos:pos + 1] != b"e":
            pos, key = _string(data, pos)
            start = pos
            pos = _skip(data, pos)
            if key == b"info":
                if span is not None or data[start:start + 1] != b"d":
                    raise ValueError
                span = (start, pos)
        if pos >= len(data) or pos + 1 != len(data) or span is None:
            raise ValueError
    except (ValueError, RecursionError):
        raise TorrentError("invalid torrent file") from None
    start, end = span
    return hashlib.sha1(data[start:end], usedforsecurity=False).hexdigest()


def file_infohash(path: str | Path) -> str:
    try:
        data = Path(path).read_bytes()
    except OSError as e:
        raise TorrentError(f"cannot read torrent file ({e.__class__.__name__})") from None
    return infohash(data)
