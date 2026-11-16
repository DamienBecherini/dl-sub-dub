import re
import tomllib
from pathlib import Path

import pytest

from subtitles import config


def test_env_reference(monkeypatch):
    monkeypatch.setenv("MY_KEY", "value")
    assert config.resolve_key("env:MY_KEY") == "value"


def test_file_reference(tmp_path):
    f = tmp_path / "key.txt"
    f.write_text("  value\n", encoding="utf-8")
    assert config.resolve_key(f"file:{f}") == "value"


def test_missing_file_without_showing_content(tmp_path):
    with pytest.raises(config.ConfigError, match="illisible"):
        config.resolve_key(f"file:{tmp_path / 'absent'}")


def test_missing_sops_suggests_the_other_forms(monkeypatch):
    monkeypatch.setattr(config.shutil, "which", lambda _: None)
    with pytest.raises(config.ConfigError, match="env:<NOM> ou file:<chemin>"):
        config.resolve_key("sops:f.env#NAME")


def test_plain_key_refused():
    with pytest.raises(config.ConfigError, match="en clair"):
        config.resolve_key("sk-plain-value")


@pytest.mark.parametrize("key", ["env:X", "file:/path/key", "sops:f.env#X", ""])
def test_references_accepted_when_saving(tmp_path, key):
    config.save({"translators": [{"id": "a", "key": key}]}, tmp_path / "c.json")


def test_plain_key_refused_when_saving(tmp_path):
    with pytest.raises(config.ConfigError, match="jamais la valeur"):
        config.save({"translators": [{"id": "a", "key": "value"}]}, tmp_path / "c.json")
    assert not (tmp_path / "c.json").exists()


def test_the_output_folder_starts_from_the_home_folder(tmp_path):
    defaults = tmp_path / "d.toml"
    defaults.write_text('output_dir = "~/Videos"\n', encoding="utf-8")
    assert "~" not in config.load(defaults, None)["output_dir"]


def test_the_user_settings_override_the_defaults(tmp_path):
    defaults, own = tmp_path / "d.toml", tmp_path / "c.json"
    defaults.write_text('output_dir = "/a"\n[web]\nport = 1\nx = 2\n', encoding="utf-8")
    own.write_text('{"output_dir": "/b", "web": {"port": 9}}', encoding="utf-8")
    cfg = config.load(defaults, own)
    assert (cfg["output_dir"], cfg["web"]) == ("/b", {"port": 9, "x": 2})


def test_defaults_without_personal_information():
    text = config.DEFAULTS_FILE.read_text(encoding="utf-8")
    for pattern in (r"\b[A-Z]:/", r"192\.168\.", r"\bCT \d+", r"Damien(?!Becherini)", "homelab", "Webdev", r"sops:[^<]"):
        assert not re.search(pattern, text), pattern


def test_no_address_of_a_home_network_nor_path_of_the_author_in_the_repository():
    """The repository is published: an address of a private network or a path of the author's machine has no place in
    it, a test included (a test names another machine with an address kept for documentation, 192.0.2.x). The real
    addresses live in the settings of the user folder, never in the repository."""
    root = Path(__file__).resolve().parent.parent
    private = re.compile(r"\b(192\.168|10\.\d{1,3}|172\.(1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b|\b[A-Z]:[/\\](Webdev|Damien|Users[/\\]damie)\b")
    found = []
    for f in root.rglob("*"):
        if f.suffix not in (".py", ".js", ".css", ".html", ".md", ".toml", ".json", ".txt", ".yml", ".yaml", ".sh", ".cfg") \
                or {".git", ".venv", "__pycache__", "node_modules", ".pytest_cache", ".ruff_cache"} & set(f.relative_to(root).parts) \
                or f.name == "failures.log":
            continue
        for n, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if private.search(line):
                found.append(f"{f.relative_to(root)}:{n}")
    assert not found, found


def test_consistent_defaults():
    with open(config.DEFAULTS_FILE, "rb") as f:
        cfg = tomllib.load(f)
    assert config.engine(cfg, "transcriptions", "active_transcription")["id"] == "example"
    assert config.engine(cfg, "translators", "active_translator")["type"] == "none"
    assert cfg["youtube"]["command"] == ["yt-dlp"]
    assert not any("supervision" in m for m in cfg["translators"])


def test_load_is_cached_until_a_file_changes(tmp_path, monkeypatch):
    """Read once while the files stay the same; a copy each time; read again after save() or another write."""
    import json as _json
    user = tmp_path / "config.json"
    user.write_text(_json.dumps({"output_dir": "A"}), encoding="utf-8")
    reads = []
    real = config._read
    monkeypatch.setattr(config, "_read", lambda d, u: reads.append(1) or real(d, u))
    a = config.load(user_file=user)
    a["output_dir"] = "changed by a caller"
    b = config.load(user_file=user)
    assert b["output_dir"] == "A" and len(reads) == 1
    config.save({"output_dir": "B"}, user)
    assert config.load(user_file=user)["output_dir"] == "B" and len(reads) == 2
    assert [f.name for f in user.parent.iterdir() if f.name.startswith(user.name)] == [user.name]   # written aside, then put in place
    user.write_text(_json.dumps({"output_dir": "CC"}), encoding="utf-8")      # written by hand: another size
    assert config.load(user_file=user)["output_dir"] == "CC" and len(reads) == 3
