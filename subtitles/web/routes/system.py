"""The machine and the engines: the graphics card shared by a local translation model and the dubbing, the working
folders cleaned, yt-dlp updated, the local transcription server stopped, a dialog box, a folder or a video opened,
an engine tested."""
import logging
import subprocess
import threading
from pathlib import Path

from ... import cleanup, config, dubbing, gpu, i18n, local_transcription, source, steps, transcription, translation, ytdlp
from .. import desktop, service
from ..router import RequestError, route

log = logging.getLogger("subtitles.web")


def test_engine(kind: str, m: dict) -> dict:
    if kind == "transcription":
        if m.get("command"):
            local_transcription.LocalServer.for_engine(m).ensure(lambda _x: None)
        return {"ok": True, "detail": i18n.t("test.responds", state=transcription.health(m["url"]))}
    if kind == "translator":
        if m.get("type") == "none":
            return {"ok": True, "detail": i18n.t("test.nothing")}
        return {"ok": True, "detail": i18n.t("test.responds", state=translation.test(m))}
    raise RequestError(400, i18n.t("err.unknown_kind"))


# --- the graphics card shared by a local translation model and the dubbing (gpu.py) ---
GPU_CHECK = {"state": "idle", "step": "", "result": None, "error": ""}       # the check under way or the last one
GPU_LOCK = threading.Lock()


def _save_dubbing(**values):
    cfg = config.load()
    cfg.setdefault("dubbing", {}).update(values)
    config.save(cfg)
    service.manager.projects.settings_changed()


@route("POST", "/api/dubbing/gpu")
def set_gpu_sharing(req):
    """{sharing: "auto"|"together"|"one_at_a_time"}: the translation model and the dubbing on the card together or
    one at a time."""
    sharing = req.json().get("sharing")
    if sharing not in gpu.MODES:
        raise RequestError(400, i18n.t("err.bad_value", value=sharing))
    _save_dubbing(gpu_sharing=sharing)
    return 200, {"sharing": gpu.sharing(config.load())}


@route("GET", "/api/dubbing/gpu-check")
def gpu_check_state(req):
    with GPU_LOCK:
        return 200, dict(GPU_CHECK)


@route("POST", "/api/dubbing/gpu-check")
def gpu_check(req):
    """{project?}: checks whether the translation model of the project (or the active one) and the dubbing fit on the
    card together, on a few seconds of a video of the project; in the background, GET for where it stands. The
    result is kept in the settings (dubbing.gpu_test) and used by the "auto" setting."""
    d = req.json()
    run = service.manager.projects.all.get(d.get("project") or "")
    cfg = config.load()
    try:
        translator = config.engine(cfg, "translators", "active_translator", (run.options.get("translator") if run else None) or None)
    except config.ConfigError:
        translator = None
    video = next((i.video for i in run.project.items if i.video and Path(i.video).is_file()), None) if run else None
    languages = [c for c in (run.subtitle_settings(cfg)["languages"] if run else cfg.get("target_languages") or ["fr"])]
    chosen = steps.dub_codes(run.options.get("dub_language")) if run else []
    target = chosen[0] if chosen else (languages[0] if languages else "fr")
    with GPU_LOCK:
        if GPU_CHECK["state"] == "running":
            raise RequestError(409, i18n.t("err.gpu_check_running"))
        GPU_CHECK.update(state="running", step="start", result=None, error="")

    def step(name):
        with GPU_LOCK:
            GPU_CHECK["step"] = name

    def check():
        try:
            result = gpu.run_test(cfg, translator, video, target, step, log=lambda m: log.info("%s", m))
            if result.get("fits") is not None:
                _save_dubbing(gpu_test=result)
            with GPU_LOCK:
                GPU_CHECK.update(state="done", result=result)
        except (dubbing.DubbingError, translation.TranslationError, source.SourceError, config.ConfigError, OSError,
                subprocess.SubprocessError) as e:
            with GPU_LOCK:
                GPU_CHECK.update(state="error", error=str(e))
    threading.Thread(target=check, daemon=True, name="gpu-check").start()
    return 202, dict(GPU_CHECK)


@route("GET", "/api/cleanup")
def cleanup_summary(req):
    """?output_dir=: size of the working folders per category."""
    cfg = config.load()
    root = Path(req.arg("output_dir", "") or cfg["output_dir"]) / ".work"
    return 200, {"root": str(root), "categories": cleanup.summary(root), "options": cleanup.options(cfg)}


@route("POST", "/api/cleanup")
def cleanup_run(req):
    """{options, output_dir?} → deleted per category."""
    d = req.json()
    cfg = config.load()
    opts = {**cleanup.options(cfg), **{k: v for k, v in (d.get("options") or {}).items() if k in cleanup.DEFAULTS}}
    root = Path(d.get("output_dir") or cfg["output_dir"]) / ".work"
    if service.manager.queue.current():
        raise RequestError(409, i18n.t("err.task_running_cleanup"))
    return 200, {"root": str(root), "deleted": cleanup.clean_all(root, opts), "categories": cleanup.summary(root)}


# --- yt-dlp: version, update ---
YTDLP_UPDATE: "ytdlp.Update | None" = None


@route("GET", "/api/ytdlp")
def ytdlp_info(req):
    """The yt-dlp of the settings: version, age, too old or not, whether it updates itself, otherwise how
    (?fresh=1: asked again rather than the answer of the last hour)."""
    return 200, ytdlp.info(fresh=req.arg("fresh", "") == "1")


@route("POST", "/api/ytdlp/update")
def ytdlp_update(req):
    """Runs `yt-dlp -U` (its own builds only; not during a download, which it would replace under its feet) → its state."""
    global YTDLP_UPDATE
    if YTDLP_UPDATE and YTDLP_UPDATE.state == "running":
        return 200, YTDLP_UPDATE.to_dict()
    if service.manager.projects.fetching.downloads:
        raise RequestError(409, i18n.t("err.ytdlp_downloading"))
    found = ytdlp.info()
    if found.get("found") and not found.get("self_update"):
        raise RequestError(409, found.get("hint") or "")
    YTDLP_UPDATE = ytdlp.Update(ytdlp.command()).start()
    return 200, YTDLP_UPDATE.to_dict()


@route("GET", "/api/ytdlp/update")
def ytdlp_update_state(req):
    """The update under way or the last one: {state (running, done, error, none), code, lines, command}."""
    return 200, YTDLP_UPDATE.to_dict() if YTDLP_UPDATE else {"state": "none", "lines": []}


# --- system, engines ---
@route("POST", "/api/local-transcription/stop")
def stop_local_transcription(req):
    """Stops the local transcription server started from here."""
    for m in config.load().get("transcriptions", []):
        if m.get("command"):
            local_transcription.LocalServer.for_engine(m).stop()
    return 200, {"ok": True}


@route("POST", "/api/window")
def application_window(req):
    """{open: bool}: the window of the application (python -m subtitles.app) opened or closed. Closed, the service
    stops as soon as its work is done (at once if it has none); opened again before that, it stays."""
    service.idle.leave(not req.json().get("open"))
    return 200, {"busy": service.busy()}


@route("POST", "/api/dialog")
def system_dialog(req):
    """{type: "file"|"folder"|"project"|"addresses"|"source_folder"|"download_folder"|"project_folder", start?} →
    {path, paths}: the first path chosen, and all of them (several videos or lists at once)."""
    d = req.json()
    paths = desktop.dialog(d.get("type", "file"), d.get("start", ""))
    return 200, {"path": paths[0] if paths else "", "paths": paths}


@route("POST", "/api/open")
def open_in_file_manager(req):
    """{path, play?}: opens the folder (file selected) in the file manager; with `play`, plays the video in the
    default player."""
    d = req.json()
    desktop.open_path(d.get("path", ""), bool(d.get("play")))
    return 200, {"ok": True}


@route("POST", "/api/test")
def test_an_engine(req):
    """{kind: "transcription"|"translator", engine: {...}}"""
    d = req.json()
    try:
        return 200, test_engine(d.get("kind", ""), d.get("engine") or {})
    except (transcription.TranscriptionError, translation.TranslationError, config.ConfigError,
            local_transcription.LocalServerError) as e:
        return 200, {"ok": False, "detail": str(e)}
