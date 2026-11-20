"""What a project is made from, and a video alone: a task for one video (/api/tasks), the inventory of a folder,
the addresses and folders read for the Select step (/api/sources), a video or a playlist of a site, the files
dropped on the page."""
import shutil
import subprocess
from pathlib import Path
from urllib.parse import unquote

from ... import config, folder, i18n, language, source, space, translation
from ...project import sources
from .. import service
from ..playlist import PlaylistDownload
from ..router import RequestError, route


def _ytdlp(cfg: dict) -> list[str]:
    return source.ytdlp_command(cfg)


# --- tasks ---
@route("GET", "/api/tasks")
def list_tasks(req):
    return 200, service.manager.queue.all_tasks()


@route("POST", "/api/tasks")
def create_task(req):
    """{source, transcription?, translator?, language?, languages?, output_dir?, destination?, redo?, pause?, spend_agreed?, until?, replace?}; a paid translator waits for a go-ahead
    unless `spend_agreed` is true; `pause` true: a free one waits too."""
    d = req.json()
    src = (d.get("source") or "").strip().strip('"')
    if not src:
        raise RequestError(400, i18n.t("err.empty_source"))
    if not source.is_url(src) and Path(src).is_dir():
        return 409, {"error": i18n.t("err.is_folder"), "folder": True}
    options = {"source": src, "transcription": d.get("transcription") or "", "translator": d.get("translator") or "",
               "language": d.get("language"), "output_dir": (d.get("output_dir") or "").strip(), "redo": bool(d.get("redo"))}
    if d.get("languages") is not None:
        options["languages"] = language.normalize_codes(d["languages"])
    if isinstance(d.get("destination"), dict):
        options["destination"] = {k: bool(d["destination"].get(k)) for k in ("next_to_video", "output_dir")}
    if d.get("pause") is True:
        options["pause"] = True
    if d.get("spend_agreed") is True:
        options["spend_agreed"] = True
    to_the_end = d.get("until") == "mux"
    if to_the_end:
        options.update(until="mux", replace=bool(d.get("replace")))
    return 201, service.manager.queue.add(options, continued=to_the_end).summary()


@route("GET", "/api/tasks/{id}")
def get_task(req, id):
    """?since=N: log lines from N."""
    d = service.manager.queue.detail(id, int(req.arg("since", "0")))
    if d is None:
        raise RequestError(404, i18n.t("err.unknown_task"))
    return 200, d


@route("GET", "/api/tasks/{id}/estimate")
def estimate_task(req, id):
    """?languages=fr,en: estimated cost of a paused task for these languages."""
    e = service.manager.queue.estimate(id, req.arg("languages"))
    if e is None:
        raise RequestError(404, i18n.t("err.task_not_prepared"))
    return 200, e


@route("POST", "/api/tasks/{id}/language")
def language_of_task(req, id):
    """{engine?}: asks a model of the settings (by default the active translator) which language is spoken in a video
    whose transcription is doubtful and waits for that → {code, engine, name} (`code` "": the model did not say).
    One short request to that model: a paid one is billed (see `opinion_models` of the estimate of the project)."""
    try:
        opinion = service.manager.queue.language_opinion(id, req.json().get("engine"))
    except (translation.TranslationError, config.ConfigError, OSError) as e:
        raise RequestError(502, str(e)) from None
    if opinion is None:
        raise RequestError(409, i18n.t("err.task_not_paused"))
    return 200, opinion


@route("POST", "/api/tasks/{id}/translate")
def translate_task(req, id):
    """{translator?, languages?}: second phase of a paused task ("none" = finish without translating)."""
    d = req.json()
    t = service.manager.queue.resume(id, d.get("translator"), d.get("languages"))
    if t is None:
        raise RequestError(409, i18n.t("err.task_not_paused"))
    return 200, t.summary()


@route("POST", "/api/tasks/{id}/retry")
def retry_task(req, id):
    """Task in error or cancelled (not a video of a project: the project retries or resumes it)."""
    t = service.manager.queue.retry(id)
    if t is None:
        raise RequestError(404, i18n.t("err.task_not_in_error"))
    return 200, {"id": t.id}


@route("POST", "/api/tasks/{id}/cancel")
def cancel_task(req, id):
    return 200, {"ok": service.manager.queue.cancel(id)}


# --- folder inventory (one at a time) ---
@route("POST", "/api/folder")
def scan_folder(req):
    """{path, recursive?}: starts the inventory in the background → {id, state…}."""
    d = req.json()
    path = Path((d.get("path") or "").strip().strip('"'))
    if not path.is_dir():
        raise RequestError(400, i18n.t("err.folder_not_found", path=path))
    if service.current_scan:
        service.current_scan.cancel()
    service.current_scan = folder.FolderScan(path, recursive=d.get("recursive", True))
    service.current_scan.start()
    return 202, service.current_scan.to_dict()


@route("GET", "/api/folder/{id}")
def get_scan(req, id):
    """Progress, languages of the audio tracks; ?videos=1: list of the videos once scanned."""
    if not service.current_scan or service.current_scan.id != id:
        raise RequestError(404, i18n.t("err.unknown_analysis"))
    return 200, service.current_scan.to_dict("videos" in req.query)


@route("POST", "/api/folder/{id}/cancel")
def cancel_scan(req, id):
    if service.current_scan and service.current_scan.id == id:
        service.current_scan.cancel()
    return 200, {"ok": True}


# --- what the Select step reads: addresses, files, folders, lists ---
@route("POST", "/api/sources/title")
def quick_title(req):
    """{url} → {title}: the title of a video address at once, for the sites that offer it (YouTube, Dailymotion,
    Vimeo), otherwise null; the page shows it while /api/sources reads the details."""
    url = (req.json().get("url") or "").strip()
    return 200, {"title": source.quick_title(url) if source.is_url(url) else None}


@route("POST", "/api/sources/location")
def default_location(req):
    """{sources: [address or path]} → {base, named, name}: where a project of these videos goes by default (the base
    folder, in a subfolder named after the project or not) and the name it takes when none is typed."""
    items = [{"source": s} for s in req.json().get("sources") or [] if str(s).strip()]
    base, named = sources.default_location(items, config.load())
    return 200, {"base": base.as_posix(), "named": named, "name": source.list_title()}


@route("POST", "/api/sources")
def resolve_sources(req):
    """{entries: [address, path of a video, of a folder or of a .txt list]} → {items: [{source, title, duration, kind,
    origin}], errors: [{entry, error}], title}: what step 1 (Select) adds. Playlists and folders are listed; nothing
    is downloaded."""
    entries = [str(e) for e in req.json().get("entries") or [] if str(e).strip()]
    if not entries:
        raise RequestError(400, i18n.t("err.no_video_selected"))
    return 200, sources.resolve(entries, _ytdlp(config.load()))


# --- files dropped on the page ---
@route("GET", "/api/files/room")
def upload_room(req):
    """?size=<bytes>&output_dir=: where dropped files would be copied, and whether the disk has room for them (with
    the margin of space.check) → {folder, free (bytes, null if unknown), enough, error}. Asked before copying."""
    target_dir = Path(req.arg("output_dir", "") or config.load()["output_dir"])
    need = int(req.arg("size", "0") or 0)
    try:
        free = shutil.disk_usage(space._existing(target_dir)).free
    except OSError:
        free = None
    try:
        space.check(target_dir, need)
        error = ""
    except space.NotEnoughSpace as e:
        error = str(e)
    return 200, {"folder": str(target_dir), "free": free, "enough": not error, "error": error}


def free_name(target: Path) -> Path:
    """`target`, or "name (2).ext", "name (3).ext"… if a file of that name is already there."""
    found, n = target, 2
    while found.exists():
        found, n = target.with_name(f"{target.stem} ({n}){target.suffix}"), n + 1
    return found


def _skip_body(req):
    while req.read(1 << 20):            # the body is read all the same: a connection closed on unread data is reset
        pass


@route("PUT", "/api/files")
def upload_file(req):
    """?name=<name>&output_dir=, body = the file (drag and drop) → copied into the output folder, if its disk has room
    for it (a file as large as the disk would fill it: refused with the free space, 507)."""
    name = Path(unquote(req.arg("name", ""))).name
    if not name:
        raise RequestError(400, i18n.t("err.missing_name"))
    target_dir = Path(req.arg("output_dir", "") or config.load()["output_dir"])
    target = target_dir / name
    if target.exists() and target.stat().st_size == req.length:
        _skip_body(req)                  # already there, same size: not rewritten
        return 200, {"path": str(target), "existed": True}
    target = free_name(target)           # another file of that name stays as it is: "name (2).ext"
    try:
        space.check(target_dir, req.length)
    except space.NotEnoughSpace as e:
        _skip_body(req)
        raise RequestError(507, str(e)) from None
    target_dir.mkdir(parents=True, exist_ok=True)
    with open(target, "wb") as f:
        while block := req.read(1 << 20):
            f.write(block)
    if req.consumed < req.length:
        target.unlink(missing_ok=True)
        raise RequestError(400, i18n.t("err.incomplete_upload"))
    return 200, {"path": str(target), "existed": False}


# --- a video or a playlist of a site, downloaded alone ---
@route("POST", "/api/youtube")
def youtube(req):
    """{url, output_dir?}: title, duration and target file, without downloading (YouTube, Dailymotion, PeerTube, Arte… sites of yt-dlp).
    A playlist: "playlist" {title, count, duration, folder}; then "video" only if the address also designates one
    video of the list (alone: "Start"; the whole list: POST /api/playlist)."""
    d = req.json()
    cfg = config.load()
    url, output_dir = d.get("url", ""), source.download_dir(cfg, d.get("output_dir"))
    try:
        p = source.playlist_info(url, _ytdlp(cfg))
        answer = {"ok": True}
        if p:
            known = [e["duration"] for e in p["entries"] if e.get("duration")]
            answer["playlist"] = {"title": p["title"], "count": len(p["entries"]), "duration": sum(known) if known else None,
                                  "folder": str(source.playlist_folder(output_dir, p)).replace("\\", "/")}
        if not p or p["with_video"]:
            i = source.youtube_info(url, _ytdlp(cfg), output_dir)
            answer.update(title=i.get("title"), duration=i.get("duration"), video=str(i["video"]), exists=i["video"].exists())
    except (source.SourceError, subprocess.SubprocessError, ValueError) as e:
        return 200, {"ok": False, "detail": str(e)}
    return 200, answer


@route("POST", "/api/playlist")
def download_playlist(req):
    """{url, output_dir?}: downloads the whole playlist in the background into <output folder>/<playlist title>
    → {id, state…}; once "done", the page opens this folder as a source folder (POST /api/folder).
    {urls: [...], title?, output_dir?}: a list of addresses (videos or playlists), downloaded the same way into
    <output folder>/<title> (default: "List <date>")."""
    d = req.json()
    cfg = config.load()
    url, urls = (d.get("url") or "").strip(), d.get("urls")
    if urls is not None:
        urls = source.address_lines("\n".join(str(u) for u in urls))
        bad = source.not_urls(urls)
        if not urls or bad:
            raise RequestError(400, i18n.t("err.not_addresses", lines=", ".join(bad[:5]) or "-"))
    elif not source.is_url(url):
        raise RequestError(400, i18n.t("err.not_a_playlist"))
    if service.current_download and service.current_download.working():
        raise RequestError(409, i18n.t("err.playlist_running"))
    title = source.folder_name(d.get("title") or source.list_title()) if urls else ""
    service.current_download = PlaylistDownload(url, _ytdlp(cfg), source.download_dir(cfg, d.get("output_dir")), urls, title)
    service.current_download.start()
    return 202, service.current_download.to_dict()


@route("POST", "/api/addresses")
def read_addresses(req):
    """{path}: the addresses of a text file, one per line → {addresses, title (file name), ignored (lines that are not
    addresses)}. Only the addresses are sent back, never the other lines of the file."""
    path = (req.json().get("path") or "").strip().strip('"')
    try:
        lines = source.read_addresses(path)
    except OSError as e:
        raise RequestError(400, str(e)) from None
    bad = source.not_urls(lines)
    return 200, {"addresses": [a for a in lines if a not in bad], "title": Path(path).stem, "ignored": len(bad)}


@route("GET", "/api/playlist/{id}")
def get_playlist(req, id):
    """Progress: state (listing, downloading, done, cancelled, error), done/total, current title, folder, failures."""
    if not service.current_download or service.current_download.id != id:
        raise RequestError(404, i18n.t("err.unknown_download"))
    return 200, service.current_download.to_dict()


@route("POST", "/api/playlist/{id}/cancel")
def cancel_playlist(req, id):
    """Stops after the video being downloaded; those already there stay in the folder."""
    if service.current_download and service.current_download.id == id:
        service.current_download.cancel()
    return 200, {"ok": True}
