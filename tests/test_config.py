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
