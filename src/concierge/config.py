"""Config storage. Secrets are DPAPI-encrypted before they touch disk."""

import base64
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


def load() -> dict:
    try:
        raw = json.loads(CONFIG_FILE.read_text())
    except FileNotFoundError:
        return dict(DEFAULTS)
    cfg = dict(DEFAULTS)
    cfg.update(raw)
    return cfg


def save(cfg: dict) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))


def get_torbox_key() -> str | None:
    enc = load()["torbox_key_enc"]
    if not enc:
        return None
    return _dpapi(base64.b64decode(enc), encrypt=False).decode()


def set_torbox_key(key: str) -> None:
    # dpapi returns raw bytes; base64 to survive json
    enc = base64.b64encode(_dpapi(key.encode(), encrypt=True)).decode()
    cfg = load()
    cfg["torbox_key_enc"] = enc
    save(cfg)


if __name__ == "__main__":
    if sys.argv[1:] == ["set-key"]:
        set_torbox_key(getpass.getpass("TorBox API key: ").strip())
        print("saved:", CONFIG_FILE)
    else:
        print("usage: python config.py set-key")
        sys.exit(1)
