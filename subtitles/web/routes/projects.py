"""/api/projects/…: a project made, read back from its file, followed (its events), its videos, their steps and
settings, the actions on it (start, suspend, stop, translate, continue, goal…). Each route checks the request and
calls the orchestrator (manager.projects), which says whether the action is possible now (409 otherwise)."""
import json
import os
import subprocess
import time
from pathlib import Path

from ... import config, i18n, media, source, steps
from ...project import Stage, sources
from ...project import settings as project_settings
from .. import last_project, service
from ..router import Raw, RequestError, Stream, not_found, route


EVENTS_GROUP_S = 0.3                           # changes this close are sent as one (a save follows another, yt-dlp writes several lines a second)
EVENTS_CLOCK_S = 1                             # while a project works, its times left move on even when nothing else does
EVENTS_BEAT_S = 15                             # nothing for this long: looked at all the same (a video file gone), and a comment
                                               # line written, so that proxies and browsers keep the stream


def _stages(names) -> list[Stage] | None:
    if names is None:
        return None
    try:
        return [Stage(n) for n in names]
    except ValueError:
        raise RequestError(400, i18n.t("err.unknown_stop_step")) from None


def _project(id: str):
    run = service.manager.projects.get(id)
    if run is None:
        raise RequestError(404, i18n.t("err.project_unknown"))
    return run


def _video_of(id: str, item: str) -> tuple[Path, dict]:
    """The video file of an item (a file, or an address once downloaded) and the item; 404 otherwise."""
    it = _project(id).project.item(item)
    if it is None or not it.video or not Path(it.video).is_file():
        raise not_found()
    return Path(it.video), it.facts


def _check_stop(stop):
    if stop not in (None, *steps.STOPS):
        raise RequestError(400, i18n.t("err.unknown_stop_step"))


def _project_after(id: str, ok: bool, action: str):
    """Answer of an action on a project: its new state, or 409 if the project refused it."""
    if not ok:
        raise RequestError(409, i18n.t("err.project_action", action=action))
    return 200, service.manager.projects.get(id).summary()


# --- projects: the last one, read back from a file, their names to check, their run ---
@route("GET", "/api/projects/last")
def get_last_project(req):
    """Project left unfinished at the previous session."""
    return 200, {"last": last_project.offered(service.manager.projects.all)}


@route("POST", "/api/projects/last/forget")
def forget_last_project(req):
    last_project.forget()
    return 200, {"ok": True}


@route("POST", "/api/projects/load")
def load_project(req):
    """{file}: reads a dl-sub-dub.project.json (or an old subtitles.batch.json) back (resume after an interruption) → suspended project, to resume."""
    try:
        return 200, service.manager.projects.load((req.json().get("file") or "").strip().strip('"')).summary()
    except source.SourceError as e:
        raise RequestError(400, str(e)) from e


@route("GET", "/api/projects/{id}/estimate")
def estimate_project(req, id):
    """?languages=fr,en"""
    if service.manager.projects.get(id) is None:
        raise RequestError(404, i18n.t("err.project_unknown"))
    return 200, service.manager.projects.estimate(id, req.arg("languages")) or {}


@route("GET", "/api/projects/{id}/review")
def review_project(req, id):
    """?path=: names to check."""
    r = service.manager.projects.proposals(id, req.arg("path"))
    if r is None:
        raise RequestError(404, i18n.t("err.project_unknown"))
    return 200, r


@route("POST", "/api/projects/{id}/validate")
def validate_project(req, id):
    """{decisions: [{transcribed, spelling, french, accept}]}"""
    r = service.manager.projects.validate_names(id, req.json().get("decisions") or [])
    if r is None:
        raise RequestError(404, i18n.t("err.project_unknown"))
    return 200, {**r, "project": service.manager.projects.get(id).summary()}


@route("POST", "/api/projects/{id}/{action:suspend|cancel|stop}")
def suspend_or_cancel_project(req, id, action):
    return _project_after(id, getattr(service.manager.projects, action)(id), action)


@route("POST", "/api/projects/{id}/translate")
def translate_project(req, id):
    """{translator?, languages?, stop?}"""
    d = req.json()
    _check_stop(d.get("stop"))
    return _project_after(id, service.manager.projects.translate(id, d.get("translator"), d.get("languages"), d.get("stop")), "translate")


@route("POST", "/api/projects/{id}/resume")
def resume_project(req, id):
    """{translator?, transcription?, languages?, pause?, stop?}"""
    return _project_after(id, service.manager.projects.resume(id, req.json()), "resume")


@route("POST", "/api/projects/{id}/goal")
def project_goal(req, id):
    """{stop, replace?}: translation (subtitles only) or mux (subtitles + dubbing)."""
    d = req.json()
    return _project_after(id, service.manager.projects.goal(id, d.get("stop") or "", d.get("replace")), "goal")


@route("POST", "/api/projects/{id}/continue")
def continue_project(req, id):
    """{path}: one video up to the end of what the project must produce, before the others."""
    return _project_after(id, service.manager.projects.continue_video(id, req.json().get("path") or ""), "continue")


@route("POST", "/api/projects/{id}/retry")
def retry_project(req, id):
    """{path?}"""
    return _project_after(id, service.manager.projects.retry(id, req.json().get("path")), "retry")


# --- a project made, followed, its videos: their steps, their settings, their tracks ---
@route("GET", "/api/projects/{id}/items/{item}/media")
def item_media(req, id, item):
    """What the video file holds (Information tab): picture, audio tracks, subtitles inside and next to it."""
    video, _facts = _video_of(id, item)
    try:
        return 200, media.info(video)
    except (media.MediaError, OSError, subprocess.TimeoutExpired) as e:
        raise RequestError(500, i18n.t("err.probe_failed", file=video.name, detail=str(e)[:300])) from None


@route("GET", "/api/projects/{id}/items/{item}/thumbnail")
def item_thumbnail(req, id, item):
    """A picture of the video (JPEG, made once by ffmpeg and kept)."""
    video, facts = _video_of(id, item)
    try:
        return 200, Raw(media.thumbnail(video, facts.get("duration")).read_bytes(), "image/jpeg")
    except (media.MediaError, OSError, subprocess.TimeoutExpired):
        raise not_found() from None


@route("GET", "/api/projects")
def list_projects(req):
    return 200, service.manager.projects.summaries()


@route("POST", "/api/projects")
def create_project(req):
    """{items: [{source, title?, duration?, language?}], stages?: ["download", "subtitles", "dubbing"], title?,
    settings?: {languages?, translator?, transcription?, pause?, review?…}, start?, location?: {base, named}} → the
    project, suspended unless `start`. `settings`: those of the project (as POST /api/projects/{id}/settings), set
    before anything starts; a script creates and runs a project in one request. Its folder: `base`, plus a subfolder
    named after the project if `named`; by default a new folder of the download folder when there are addresses,
    otherwise the folder the files share."""
    d = req.json()
    items = [i for i in d.get("items") or [] if str(i.get("source") or "").strip()]
    if not items:
        raise RequestError(400, i18n.t("err.no_video_selected"))
    chosen = d.get("settings") or {}
    if not isinstance(chosen, dict) or not set(chosen) <= project_settings.PROJECT_KEYS:
        raise RequestError(400, i18n.t("err.project_action", action="settings"))
    for i in items:
        if not source.is_url(i["source"]) and not Path(i["source"]).is_file():
            raise RequestError(400, i18n.t("err.file_not_found", path=i["source"]))
    stages = _stages(d.get("stages"))
    if stages is not None and not any(source.is_url(i["source"]) for i in items):
        stages = [s for s in stages if s != Stage.DOWNLOAD]
    cfg = config.load()
    title = (d.get("title") or "").strip() or source.list_title()
    where = d.get("location") or {}
    base = str(where.get("base") or "").strip()
    if base:
        if not Path(os.path.expanduser(base)).is_absolute():
            raise RequestError(400, i18n.t("err.folder_not_absolute", path=base))
        if Path(os.path.expanduser(base)).exists() and not Path(os.path.expanduser(base)).is_dir():
            raise RequestError(400, i18n.t("err.not_a_folder", path=base))
    named = where.get("named")
    root = sources.project_folder(items, title, cfg, base=base or None, named=None if named is None else bool(named))
    run = service.manager.projects.create(root.as_posix(), items, {}, title, stages, start=False)
    if chosen:
        service.manager.projects.set_project_settings(run.id, chosen)
    if d.get("start"):
        service.manager.projects.start(run.id)
    return 201, run.summary()


@route("POST", "/api/projects/{id}/location")
def project_location(req, id):
    """{title?, base?, named?} → the project renamed, and moved to `base` (plus a subfolder named after it if
    `named`) while nothing has started (409 after: only the name changes then)."""
    _project(id)
    d = req.json()
    named = d.get("named")
    reason = service.manager.projects.relocate(id, d.get("title"), d.get("base"), None if named is None else bool(named))
    if reason:
        raise RequestError(409 if reason in ("err.project_started_move", "err.project_already_there") else 400,
                           i18n.t(reason, path=str(d.get("base") or "")))
    return 200, _project(id).summary()


@route("GET", "/api/projects/{id}")
def get_project(req, id):
    return 200, _project(id).summary()


@route("GET", "/api/projects/{id}/events")
def project_events(req, id):
    """Server-Sent Events: the summary of the project (as GET /api/projects/{id}) each time it changes; "gone" when
    the project is no longer known (service restarted without it). The stream sleeps until the project says it
    changed (ProjectRun.changes): nothing is built nor compared while nothing happens."""
    _project(id)

    def events():
        last, beat, seen = None, time.monotonic(), None
        while True:
            run = service.manager.projects.all.get(id)
            if run is None:
                yield "event: gone\ndata: {}\n\n"
                return
            seen = run.changes.count if seen is None else seen
            summary = run.summary()
            body = json.dumps(summary, ensure_ascii=False)
            if body != last:
                last, beat = body, time.monotonic()
                yield f"data: {body}\n\n"
            elif time.monotonic() - beat > EVENTS_BEAT_S:
                beat = time.monotonic()
                yield ": beat\n\n"
            working = summary["rules"]["working"] or bool(summary["downloads"])
            before = seen
            seen = run.changes.wait(seen, EVENTS_CLOCK_S if working else EVENTS_BEAT_S)
            if seen != before:
                time.sleep(EVENTS_GROUP_S)
                seen = run.changes.count
    return 200, Stream(events())


@route("POST", "/api/projects/{id}/items")
def add_project_items(req, id):
    """{items: [{source, title?, duration?}]}: back to Select, more videos → {added, already, project}; `already`:
    those the project held ([{source, title, kind}], with the title of the line that holds each)."""
    run = _project(id)
    items = [i for i in req.json().get("items") or [] if i.get("source")]
    already = service.manager.projects.already_there(id, items)
    added = service.manager.projects.add_items(id, items)
    return 200, {"added": added, "already": already, "project": run.summary()}


@route("POST", "/api/projects/{id}/start")
def start_project(req, id):
    """{stages?, only?}: runs the steps ticked on the diagram (or these ones, which become the ticked ones; with
    `only`, these ones without changing the ticks: "Run this step") → the project; 409 if it is already running or
    cancelled."""
    _project(id)
    d = req.json()
    return _project_after(id, service.manager.projects.start(id, _stages(d.get("stages")), bool(d.get("only"))), "start")


@route("POST", "/api/projects/{id}/stages")
def set_project_stages(req, id):
    """{stages}: the steps ticked on the diagram → the project."""
    _project(id)
    return _project_after(id, service.manager.projects.set_stages(id, _stages(req.json().get("stages") or [])), "stages")


@route("POST", "/api/projects/{id}/select")
def select_project_items(req, id):
    """{items: {item id: ticked}, stage?}: ticks for one step (its screen), or for every step (Select) → the project."""
    _project(id)
    d = req.json()
    stage = _stages([d["stage"]])[0] if d.get("stage") else None
    choices = d.get("items") or {}
    return _project_after(id, service.manager.projects.select(id, {str(k): bool(v) for k, v in choices.items()}, stage), "select")


@route("POST", "/api/projects/{id}/items/{item}/settings")
def set_item_settings(req, id, item):
    """{key: value}: settings of this video only (language, languages, max_height, audio, site_subtitles; null: the
    project's) → the project."""
    _project(id)
    return _project_after(id, service.manager.projects.set_item_settings(id, item, req.json()), "settings")


@route("POST", "/api/projects/{id}/settings")
def set_project_settings(req, id):
    """{key: value}: settings of the project (null: the configuration's) → the project."""
    _project(id)
    return _project_after(id, service.manager.projects.set_project_settings(id, req.json()), "settings")


@route("POST", "/api/projects/{id}/items/{item}/tracks")
def add_item_tracks(req, id, item):
    """{audio: [language], subtitles: [{language, auto}]}: tracks of the site added to a video already downloaded,
    without downloading it again → the project; 409 if not possible (not downloaded, already adding…)."""
    _project(id)
    d = req.json()
    return _project_after(id, service.manager.projects.fetching.add_tracks(id, item, d.get("audio") or [], d.get("subtitles") or []), "tracks")


@route("GET", "/api/projects/{id}/items/{item}/log")
def item_download_log(req, id, item):
    """The log of the download of a video, as a terminal shows it (the one under way, otherwise the last one)."""
    _project(id)
    return 200, {"lines": service.manager.projects.fetching.log(item)}


@route("POST", "/api/projects/{id}/items/{item}/info")
def read_item_info_again(req, id, item):
    """Reads the information of the site again (after an error); for a video file whose address is known, reads it
    for the first time (it is read only when asked) → the project."""
    _project(id)
    return _project_after(id, service.manager.projects.fetching.read_info_again(id, item), "info")


@route("POST", "/api/projects/{id}/items/{item}/origin")
def set_item_origin(req, id, item):
    """{address}: the address a video file comes from, given by the user ("": forgotten, the one written in the file is
    read again) → the project; 409 for an address of the list, or anything but an http(s) address."""
    _project(id)
    return _project_after(id, service.manager.projects.fetching.set_origin(id, item, str(req.json().get("address") or "")), "origin")


@route("POST", "/api/projects/{id}/items/{item}/{action:run|cancel}")
def act_on_project_item(req, id, item, action):
    """{stage}: one step of one video, now: run (also Retry, and Translate when its translation waits) or cancel →
    the project; 409 if it is not possible (running already, address not downloaded, nothing to cancel…). Run with
    {stages: [...]}: those steps one after the other, each started when the one before ends."""
    _project(id)
    body = req.json()
    if action == "run" and body.get("stages"):             # several, one after the other (the ▾ menu)
        return _project_after(id, service.manager.projects.run_chain(id, item, _stages([str(s) for s in body["stages"]])), action)
    stage = _stages([body.get("stage") or ""])[0]
    act = service.manager.projects.run_item if action == "run" else service.manager.projects.cancel_item
    return _project_after(id, act(id, item, stage), action)


@route("POST", "/api/projects/{id}/remove")
def remove_project_items(req, id):
    """{ids: [item id]}: out of the project (not those being worked on) → {removed, project}."""
    run = _project(id)
    removed = service.manager.projects.remove_items(id, [str(i) for i in req.json().get("ids") or []])
    return 200, {"removed": removed, "project": run.summary()}


@route("POST", "/api/projects/{id}/gpu-freed")
def project_gpu_freed(req, id):
    """The user stopped the translation model: the dubbing of the project goes on."""
    _project(id)
    return _project_after(id, service.manager.projects.gpu_freed(id), "gpu-freed")
