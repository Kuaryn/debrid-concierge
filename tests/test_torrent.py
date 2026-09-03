import hashlib

import pytest

from concierge import torrent

INFO = b"d4:name1:xe"
TORRENT = b"d4:info" + INFO + b"e"


def test_infohash_uses_the_exact_info_bytes():
    expected = hashlib.sha1(INFO, usedforsecurity=False).hexdigest()
    assert torrent.infohash(TORRENT) == expected


def test_top_level_metadata_does_not_change_infohash():
    first = b"d8:announce3:one4:info" + INFO + b"e"
    second = b"d7:comment5:hello4:info" + INFO + b"e"
    assert torrent.infohash(first) == torrent.infohash(second)


def test_different_info_dictionary_changes_infohash():
    other = b"d4:infod4:name1:yee"
    assert torrent.infohash(TORRENT) != torrent.infohash(other)


def test_non_utf8_values_are_hashed_as_raw_bytes():
    info = b"d4:name1:\xffe"
    value = b"d4:info" + info + b"e"
    expected = hashlib.sha1(info, usedforsecurity=False).hexdigest()
    assert torrent.infohash(value) == expected


def test_invalid_bencode_is_rejected():
    bad = [
        b"", b"le", b"d4:infoe", b"d4:info1:xe",
        b"d4:info" + INFO + b"4:info" + INFO + b"e",
        TORRENT + b"junk", b"d4:infod1:ai03eee", b"d4:infod1:a3:xe",
    ]
    for value in bad:
        with pytest.raises(torrent.TorrentError, match="invalid torrent file"):
            torrent.infohash(value)


def test_deeply_nested_value_is_rejected():
    value = b"d4:info" + b"l" * torrent.MAX_DEPTH + b"e" * torrent.MAX_DEPTH + b"e"
    with pytest.raises(torrent.TorrentError, match="invalid torrent file"):
        torrent.infohash(value)


def test_missing_file_has_safe_error(tmp_path):
    with pytest.raises(torrent.TorrentError, match=r"cannot read torrent file \(FileNotFoundError\)"):
        torrent.file_infohash(tmp_path / "missing.torrent")
