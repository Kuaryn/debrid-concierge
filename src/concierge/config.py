"""Config storage. Secrets are DPAPI-encrypted before they touch disk."""

import base64
import binascii
import ctypes
import ctypes.wintypes
import getpass
import json
import os
import sys
from pathlib import Path

# appdata is missing off-windows; fall back so tests can import this module
APP_DIR = Path(os.environ.get("APPDATA") or Path.home()) / "debrid-concierge"
CONFIG_FILE = APP_DIR / "config.json"

DEFAULTS = {"torbox_key_enc": None, "last_folder": None}


class ConfigError(Exception):
    pass


class DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", ctypes.wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    ]


def _dpapi(data: bytes, encrypt: bool) -> bytes:
    # per-user encryption: a copied config file won't decrypt anywhere else
    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = DATA_BLOB()
    func = (
        ctypes.windll.crypt32.CryptProtectData if encrypt
        else ctypes.windll.crypt32.CryptUnprotectData
    )
    if not func(ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)):
        raise OSError("DPAPI call failed")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def preserve_bad(path: Path) -> Path:
    data = path.read_bytes()
    base = path.with_name(path.name + ".bad")
    number = 0
    while True:
        candidate = base if number == 0 else base.with_name(base.name + f".{number}")
        try:
            with candidate.open("xb") as fh:
                fh.write(data)
            break
        except FileExistsError:
            number += 1
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    return candidate


def _invalid_config() -> None:
    try:
        backup = preserve_bad(CONFIG_FILE)
    except OSError:
        raise ConfigError("config file is invalid and could not be preserved") from None
    raise ConfigError(f"config file is invalid; the original is in {backup.name}")


def load() -> dict:
    try:
        text = CONFIG_FILE.read_text()
    except FileNotFoundError:
        return dict(DEFAULTS)
    except (OSError, UnicodeError):
        raise ConfigError("config file could not be read") from None
    try:
        raw = json.loads(text)
    except ValueError:
        _invalid_config()
    if (not isinstance(raw, dict) or any(k not in DEFAULTS for k in raw)
            or any(v is not None and not isinstance(v, str) for v in raw.values())):
        _invalid_config()
    cfg = dict(DEFAULTS)
    cfg.update(raw)
    return cfg


def save(cfg: dict) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    # tmp + replace: a crash mid-write must not destroy the config
    tmp = CONFIG_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, indent=2))
    os.replace(tmp, CONFIG_FILE)


def get_torbox_key() -> str | None:
    enc = load()["torbox_key_enc"]
    if not enc:
        return None
    try:
        encrypted = base64.b64decode(enc, validate=True)
        key = _dpapi(encrypted, encrypt=False).decode()
    except (binascii.Error, OSError, UnicodeError, ValueError):
        raise ConfigError("TorBox key is missing or invalid; save it again") from None
    return key or None


def set_torbox_key(key: str) -> None:
    # dpapi returns raw bytes; base64 to survive json
    enc = base64.b64encode(_dpapi(key.encode(), encrypt=True)).decode()
    try:
        cfg = load()
    except ConfigError:
        if CONFIG_FILE.exists():
            raise
        cfg = dict(DEFAULTS)
    cfg["torbox_key_enc"] = enc
    save(cfg)


if __name__ == "__main__":
    if sys.argv[1:] == ["set-key"]:
        set_torbox_key(getpass.getpass("TorBox API key: ").strip())
        print("saved:", CONFIG_FILE)
    else:
        print("usage: python config.py set-key")
        sys.exit(1)
