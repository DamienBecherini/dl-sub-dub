"""The page and what it reads first: its files, its texts in each language, the state of the service, the settings."""
import json
import urllib.error
import urllib.request
from pathlib import Path

from ... import config, i18n, local_transcription
from .. import service
from ..router import Raw, not_found, route


STATIC = Path(__file__).resolve().parent.parent / "static"
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".svg": "image/svg+xml", ".png": "image/png"}


def local_ai_state(cfg: dict) -> dict | None:
    for m in cfg.get("translators", []):
        if m.get("supervision"):
            try:
                with urllib.request.urlopen(m["supervision"].rstrip("/") + "/api/ai", timeout=5) as r:   # nvidia-smi ≈ 1 s
                    d = json.load(r)
                return {"state": d.get("state"), "models": [x.get("id") for x in d.get("models", [])]}
            except (urllib.error.URLError, OSError, ValueError):
                return {"state": "unknown"}
    return None


def local_transcription_state(cfg: dict) -> dict:
    r = {}
    for m in cfg.get("transcriptions", []):
        if m.get("command"):
            r[m["id"]] = local_transcription.LocalServer.for_engine(m).state()
    return r


# --- page, catalogs, state ---
@route("GET", "/")
def page(req):
    """The page of the four steps (Select, Download, Subtitles, Dubbing)."""
    return static_file(req, "v2/index.html")


@route("GET", "/static/{name:.+}")
def static_file(req, name):
    f = (STATIC / name).resolve()
    if not f.is_file() or STATIC not in f.parents:
        raise not_found()
    return 200, Raw(f.read_bytes(), TYPES.get(f.suffix, "application/octet-stream"))


@route("GET", "/locales/{name:.+}")
def catalog(req, name):
    """Translation catalog of a language (the same files as the messages of the server)."""
    code = name.removesuffix(".json")
    if code not in i18n.available():
        raise not_found()
    return 200, Raw((i18n.LOCALES_DIR / f"{code}.json").read_bytes(), "application/json; charset=utf-8")


@route("GET", "/api/languages")
def languages(req):
    """Available interface languages and setting."""
    setting = config.load().get("ui_language") or ""
    return 200, {"available": i18n.available(), "setting": i18n.pick(setting) or ""}


@route("GET", "/api/health")
def get_health(req):
    """"local service" contract v2: {state, contract, service, version, busy, activity, remaining_work_s, idle}."""
    return 200, service.health()


@route("GET", "/api/status")
def status(req):
    """Active engines, state of the local transcription server and of the local AI, current task."""
    cfg = config.load()
    current = service.manager.queue.current() or next(iter(reversed(service.manager.queue.paused())), None)
    run = service.manager.projects.active()
    return 200, {"project": run.summary() if run else None,
                 "active_transcription": cfg.get("active_transcription"), "active_translator": cfg.get("active_translator"),
                 "output_dir": cfg.get("output_dir"), "language": cfg.get("language", ""),
                 "local_transcription": local_transcription_state(cfg), "local_ai": local_ai_state(cfg),
                 "task": current.summary() if current else None, "user_config": str(config.USER_FILE)}


@route("GET", "/api/config")
def get_config(req):
    return 200, config.load()


@route("PUT", "/api/config")
def put_config(req):
    """ui_language: language imposed on the page and the log, empty = automatic."""
    cfg = req.json()
    config.save(cfg)
    i18n.set_language(cfg.get("ui_language"))
    service.manager.projects.settings_changed()
    return 200, config.load()


@route("GET", "/v2")
def page_v2(req):
    """The address of the page while it was being built, kept for the bookmarks."""
    return static_file(req, "v2/index.html")


@route("GET", "/settings")
def page_settings(req):
    return static_file(req, "settings.html")
