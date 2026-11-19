"""Configuration: package defaults (subtitles/config.toml) overridden by the user settings (config.json in the user
folder, written by the web interface). Keys are never stored in clear text: a reference "env:<NAME>",
"file:<path>" (file that holds the key) or "sops:<file>#<NAME>" (if sops is installed)."""
import copy
import json
import os
import threading
import shutil
import subprocess
import tomllib
from pathlib import Path

from . import i18n

DEFAULTS_FILE = Path(__file__).resolve().parent / "config.toml"


def state_dir() -> Path:
    """The fixed folder of the application on this machine (%LOCALAPPDATA%\\subtitles): its log, lock, window and
    thumbnails, and location.json, which says where the user's data is."""
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "subtitles"


LOCATION_FILE = "location.json"


def user_dir() -> Path:
    """The folder of the user's data (settings, history, speeds, last project): the one location.json names (chosen
    in Settings, see data_location.py), else the fixed folder."""
    try:
        data = json.loads((state_dir() / LOCATION_FILE).read_text(encoding="utf-8")).get("data")
    except (OSError, ValueError, AttributeError):
        data = None
    return Path(os.path.expandvars(data)) if data else state_dir()


USER_FILE = user_dir() / "config.json"       # changed when the data moves (data_location.repoint)
_CURRENT = object()                          # default of load and save: USER_FILE as it is at the time of the call
REFERENCES = ("env:", "file:", "sops:")


class ConfigError(Exception):
    pass


def _stamp(file: Path | None):
    """What changes when a file is written: its time and size (None: no file)."""
    try:
        st = file.stat()
        return st.st_mtime_ns, st.st_size
    except (OSError, AttributeError):
        return None


_CACHE: dict = {}                    # (files, their stamps) -> settings read: the page asks often (each event of a project)
_CACHE_LOCK = threading.Lock()
_SAVE_LOCK = threading.Lock()


def load(defaults_file: Path = DEFAULTS_FILE, user_file=_CURRENT) -> dict:
    """The settings: read again only when one of the two files changed (its time or size), a copy each time (the
    callers change what they get). user_file None: the defaults alone."""
    if user_file is _CURRENT:
        user_file = USER_FILE
    key = (str(defaults_file), str(user_file), _stamp(defaults_file), _stamp(user_file))
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
    if cached is None:
        cached = _read(defaults_file, user_file)
        with _CACHE_LOCK:
            _CACHE.clear()               # only the last one: an older stamp will not come back
            _CACHE[key] = cached
    return copy.deepcopy(cached)


def _read(defaults_file: Path, user_file: Path | None) -> dict:
    with open(defaults_file, "rb") as f:
        cfg = tomllib.load(f)
    own = {}
    if user_file and user_file.exists():
        own = json.loads(user_file.read_text(encoding="utf-8"))
        cfg = merge(cfg, own)
    if cfg.get("output_dir"):
        cfg["output_dir"] = os.path.expanduser(cfg["output_dir"])
    if not (user_file and user_file.exists() and "target_languages" in own):
        cfg["target_languages"] = default_languages(cfg)
    return cfg


def default_languages(cfg: dict) -> list[str]:
    """The languages of a new project until the user chooses some (Settings): the interface's and English."""
    first = i18n.pick(cfg.get("ui_language")) or i18n.system_language()
    return list(dict.fromkeys([first, "en"]))


def merge(base: dict, over: dict) -> dict:
    """Tables are merged key by key; lists (engines) and plain values are replaced."""
    r = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(r.get(k), dict):
            r[k] = merge(r[k], v)
        else:
            r[k] = v
    return r


def save(cfg: dict, file=_CURRENT):
    if file is _CURRENT:
        file = USER_FILE
    for m in cfg.get("transcriptions", []) + cfg.get("translators", []):
        key = str(m.get("key", ""))
        if key and not key.startswith(REFERENCES):
            raise ConfigError(i18n.t("err.key_value_refused", name=m.get("name", m.get("id")), forms=i18n.t("cfg.key_forms")))
    file.parent.mkdir(parents=True, exist_ok=True)
    # Written aside, then put in place in one go: a service stopped midway never leaves half a file of settings.
    partial = file.with_name(file.name + ".partial")
    with _SAVE_LOCK:                     # two saves at once (the page, the check of the card) share that file
        partial.write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(partial, file)
    with _CACHE_LOCK:
        _CACHE.clear()                   # even if the time of the file did not move (a coarse clock)


def engine_name(m: dict) -> str:
    """Displayed name of an engine: its `name` field, else a translated name from its id (bundled engines), else the id."""
    return m.get("name") or i18n.label("engine.", str(m.get("id", "")))


def engine(cfg: dict, list_key: str, active_key: str, ident: str | None = None) -> dict:
    """Entry of cfg[list_key] whose id is `ident` (or cfg[active_key])."""
    wanted = ident or cfg.get(active_key)
    for m in cfg.get(list_key, []):
        if m.get("id") == wanted:
            return m
    raise ConfigError(i18n.t("err.not_in_list", id=wanted, list=list_key))


def resolve_key(reference: str) -> str:
    """Value of a key from its reference. Never logged by the callers."""
    reference = (reference or "").strip()
    if not reference:
        return ""
    if reference.startswith("env:"):
        return os.environ.get(reference[4:], "")
    if reference.startswith("file:"):
        path = Path(os.path.expanduser(reference[5:]))
        try:
            return path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError) as e:
            raise ConfigError(i18n.t("err.key_file_unreadable", path=path, error=type(e).__name__)) from e
    if reference.startswith("sops:"):
        file, _, name = reference[5:].partition("#")
        if not name:
            raise ConfigError(i18n.t("err.sops_no_name", ref=reference))
        sops = shutil.which("sops")
        if not sops:
            raise ConfigError(i18n.t("err.sops_missing"))
        r = subprocess.run([sops, "-d", "--extract", f'["{name}"]', file], capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        if r.returncode != 0:
            raise ConfigError(i18n.t("err.sops_failed", name=name, file=file, detail=r.stderr.strip()[-200:]))
        return r.stdout.strip()
    raise ConfigError(i18n.t("err.plain_key", forms=i18n.t("cfg.key_forms")))
