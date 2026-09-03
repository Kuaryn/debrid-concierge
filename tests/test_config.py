import json

import pytest

from concierge import config


def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "APP_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")


def test_save_load_roundtrip(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    config.save({"last_folder": "C:/dl", "torbox_key_enc": None})
    assert config.load()["last_folder"] == "C:/dl"


def test_save_leaves_no_tmp_and_valid_json(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    config.save({"last_folder": None})
    assert config.CONFIG_FILE.exists()
    assert not (tmp_path / "config.json.tmp").exists()
    assert config.CONFIG_FILE.read_text().endswith("}")


def test_broken_config_is_preserved(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    config.CONFIG_FILE.write_text("{")

    with pytest.raises(config.ConfigError, match="config file is invalid"):
        config.load()

    assert not config.CONFIG_FILE.exists()
    assert (tmp_path / "config.json.bad").read_text() == "{"


def test_config_backup_is_not_overwritten(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    config.CONFIG_FILE.write_text("[]")
    (tmp_path / "config.json.bad").write_text("older")

    with pytest.raises(config.ConfigError):
        config.load()

    assert (tmp_path / "config.json.bad").read_text() == "older"
    assert (tmp_path / "config.json.bad.1").read_text() == "[]"


def test_config_rejects_unknown_and_bad_values(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    config.CONFIG_FILE.write_text(json.dumps({"last_folder": 7, "extra": "x"}))

    with pytest.raises(config.ConfigError):
        config.load()

    assert (tmp_path / "config.json.bad").exists()


def test_bad_encrypted_key_has_plain_error(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    config.save({"torbox_key_enc": "not base64", "last_folder": None})

    with pytest.raises(config.ConfigError, match="key is missing or invalid"):
        config.get_torbox_key()


def test_dpapi_failure_has_plain_error(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    config.save({"torbox_key_enc": "YWJj", "last_folder": None})

    def fail_dpapi(data, encrypt):
        raise OSError

    monkeypatch.setattr(config, "_dpapi", fail_dpapi)

    with pytest.raises(config.ConfigError, match="key is missing or invalid"):
        config.get_torbox_key()


def test_set_key_recovers_after_broken_config(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    config.CONFIG_FILE.write_text("{")
    monkeypatch.setattr(config, "_dpapi", lambda data, encrypt: b"encrypted")

    config.set_torbox_key("new key")

    assert config.load()["torbox_key_enc"] == "ZW5jcnlwdGVk"
    assert (tmp_path / "config.json.bad").read_text() == "{"
