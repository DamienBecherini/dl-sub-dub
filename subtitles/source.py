"""Getting the video and its audio: YouTube address (yt-dlp) or file on the disk (ffmpeg).

Paths always with forward slashes (pathlib): the same code runs on Windows and in WSL.
"""
import json
import os
import re
import shutil
import subprocess
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import cancellation, i18n


class SourceError(Exception):
    pass


class DownloadCancelled(SourceError):
    """The download was stopped on request: yt-dlp killed, its partial files deleted, or kept for a pause (`partial`:
    the video they belong to)."""
    partial: Path | None = None


AUDIO_EXTENSIONS = {".m4a", ".opus", ".mp3", ".ogg", ".flac", ".wav"}


def is_extracted_audio(p: Path, base: str) -> bool:
    """Audio file of a working folder: <base>.audio.<ext>, or <base>.<ext> (name used by the first versions)."""
    return p.suffix.lower() in AUDIO_EXTENSIONS and (p.name.startswith(base + ".audio.") or p.stem == base)


def is_url(s: str) -> bool:
    return re.match(r"https?://", s) is not None


def tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise SourceError(i18n.t("err.not_in_path", name=name))
    return path


def slug(title: str, max_len: int = 80) -> str:
    """Safe folder and file name: letters without accents, digits, dots and hyphens."""
    no_accents = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    s = re.sub(r"[^A-Za-z0-9.]+", "-", no_accents).strip("-.")
    s = re.sub(r"-{2,}", "-", s)
    return s[:max_len].rstrip("-.") or "video"


def _ffprobe(file: Path) -> dict:
    r = subprocess.run([tool("ffprobe"), "-v", "error", "-print_format", "json", "-show_streams", "-show_format",
                        str(file)], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise SourceError(f"ffprobe : {r.stderr.strip()[-300:]}")
    return json.loads(r.stdout)


def stream_types(file: Path) -> set[str]:
    return {s.get("codec_type") for s in _ffprobe(file).get("streams", [])}


def duration(file: Path) -> float:
    try:
        return float(_ffprobe(file)["format"]["duration"])
    except (KeyError, ValueError):
        return 0.0


# Container that accepts the codec as it is (copy without re-encoding); otherwise re-encoding to AAC.
_CONTAINERS = {"aac": "m4a", "opus": "opus", "mp3": "mp3", "vorbis": "ogg", "flac": "flac"}


def extract_audio(video: Path, target_without_extension: Path) -> Path:
    """Audio track of the video in a separate file (stream copy when the container allows it)."""
    streams = [s for s in _ffprobe(video).get("streams", []) if s.get("codec_type") == "audio"]
    if not streams:
        raise SourceError(i18n.t("err.no_audio_track", path=video))
    codec = streams[0].get("codec_name", "")
    ext = _CONTAINERS.get(codec)
    # Not with_suffix: "name.audio" would lose its ".audio" (the file must be called <name>.audio.<ext>)
    target = target_without_extension.parent / f"{target_without_extension.name}.{ext or 'm4a'}"
    codec_args = ["-c:a", "copy"] if ext else ["-c:a", "aac", "-b:a", "128k"]
    r = subprocess.run([tool("ffmpeg"), "-y", "-nostdin", "-hide_banner", "-loglevel", "error", "-i", str(video),
                        "-vn", "-map", "0:a:0", *codec_args, str(target)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0 or not target.exists():
        detail = r.stderr.strip()[-300:]
        if not detail and os.name == "nt" and len(str(target)) >= 260:      # Windows: ffmpeg writes nothing, and says nothing
            detail = i18n.t("err.path_too_long", n=len(str(target)), path=target.parent)
        raise SourceError(f"ffmpeg : {detail}")
    return target


def _command(command: list[str]) -> list[str]:
    """Checks the first word of the command (wsl or yt-dlp) in the PATH."""
    return [tool(command[0]), *command[1:]]


def local_path(path: str, command: list[str]) -> Path:
    """A path given by yt-dlp in WSL (/mnt/d/…) becomes a Windows path (D:/…) when the command goes through wsl."""
    if os.name == "nt" and Path(command[0]).stem.lower() == "wsl":
        # Symbolic links (~/ytdl → /mnt/d/…) are resolved in WSL; the file may not exist yet.
        r = subprocess.run([command[0], "-e", "readlink", "-m", path], capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        if r.returncode == 0 and r.stdout.strip():
            path = r.stdout.strip()
        m = re.match(r"^/mnt/([a-zA-Z])/(.*)$", path)
        if not m:
            raise SourceError(i18n.t("err.ytdlp_outside_windows", path=path))
        return Path(f"{m.group(1).upper()}:/{m.group(2)}")
    return Path(path)


def remote_path(path: Path, command: list[str]) -> str:
    """Windows path → path seen by yt-dlp in WSL (D:/x → /mnt/d/x) when the command goes through wsl."""
    s = path.as_posix()
    if os.name == "nt" and Path(command[0]).stem.lower() == "wsl":
        m = re.match(r"^([a-zA-Z]):/(.*)$", s)
        if not m:
            raise SourceError(i18n.t("err.output_outside_windows", path=path))
        return f"/mnt/{m.group(1).lower()}/{m.group(2)}"
    return s


# A file name holds 255 bytes on about every disk, and yt-dlp does not shorten a title by itself (a title of 96
# Japanese characters is 288 bytes: "File name too long"). The title is cut in bytes, which leaves room for the number
# of the video ("001 - "), the extension and what yt-dlp adds while it downloads (".f137.mp4.part-Frag12.part").
TITLE_BYTES = 180
TITLE = f"%(title).{TITLE_BYTES}B"             # yt-dlp template of a file name, without the extension
WINDOWS_PATH = 259                             # characters of a whole path, for the programs that stop at 260
AFTER_TITLE = 32                               # room kept after the title in such a path (extensions, ".multi.mkv.part")
SHORTEST_TITLE = 30                            # under this, the folder is refused rather than the title cut to nothing
_TOO_LONG = re.compile(r"File name too long|Errno 36|WinError 206", re.I)


def name_template(folder: Path | None = None, prefix: str = "") -> str:
    """yt-dlp template of the name of a downloaded file, without the extension: `prefix` then the title, cut to what
    a file name holds. On Windows the whole path counts too: in a folder with a long path the title is cut shorter,
    and a folder too deep for any title is refused. The same folder and title always give the same name, so a video
    already downloaded is found again."""
    room = TITLE_BYTES
    if os.name == "nt" and folder is not None:
        room = min(room, WINDOWS_PATH - len(str(folder)) - 1 - len(prefix) - AFTER_TITLE)
        if room < SHORTEST_TITLE:
            raise SourceError(i18n.t("err.folder_too_long", n=len(str(folder)), path=folder))
    return f"{prefix}%(title).{room}B"


def ytdlp_format(max_height: int = 0, max_audio_kbps: int = 0) -> str:
    """yt-dlp -f: best image up to `max_height` with sound up to `max_audio_kbps`, falling back step by step down to
    the best file there is (a site without these sizes still gives its video)."""
    image = f"bv*[height<={max_height}]" if max_height else "bv*"
    sound = f"ba[abr<={max_audio_kbps}]" if max_audio_kbps else "ba"
    whole = f"b[height<={max_height}]" if max_height else "b"
    choices = [f"{image}+{sound}", f"{image}+ba", whole, "bv*+ba", "b"]
    return "/".join(dict.fromkeys(choices))


def ytdlp_command(cfg: dict) -> list[str]:
    """Configured yt-dlp command followed by the download settings ([youtube] table)."""
    y = cfg.get("youtube") or {}
    options = ["-f", ytdlp_format(int(y.get("max_height") or 0), int(y.get("max_audio_kbps") or 0)),
               "--embed-metadata"]                 # title, date and the address of the page written in the video file
    if y.get("merge_format"):
        options += ["--merge-output-format", str(y["merge_format"])]
    if int(y.get("retries") or 0):
        options += ["--retries", str(int(y["retries"])), "--fragment-retries", str(int(y["retries"]))]
    return [*(y.get("command") or ["yt-dlp"]), *options, *[str(a) for a in y.get("extra_args") or []]]


def download_dir(cfg: dict, output_dir: str | Path | None = None) -> Path:
    """Folder of the downloaded videos: `download_dir` of [youtube], otherwise the output folder."""
    return Path(os.path.expanduser((cfg.get("youtube") or {}).get("download_dir") or output_dir or cfg["output_dir"]))


def _output_options(command: list[str], folder: Path | None, name: str | None = None) -> list[str]:
    """Replaces the -o of the yt-dlp config by the requested output folder (name = title, final extension)."""
    if folder is None:
        return []
    return ["-o", remote_path(folder, command) + f"/{name or name_template(folder)}.%(ext)s"]


def youtube_info(url: str, command: list[str], folder: Path | None = None, name: str | None = None) -> dict:
    """Id, title, duration (seconds) and target file, without downloading. Any site yt-dlp knows, not only YouTube.
    The address of a whole playlist is refused: one line per video would come back, and the download would take them all."""
    r = subprocess.run([*_command(command), "--no-playlist", "--no-download", *_output_options(command, folder, name),
                        "--print", "%(.{id,title,duration,filename})j", url],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise SourceError(f"yt-dlp : {(r.stderr or r.stdout).strip()[-500:]}")
    lines = r.stdout.strip().splitlines()
    if len(lines) > 1:
        raise SourceError(i18n.t("err.is_playlist", n=len(lines)))
    info = json.loads(lines[-1])
    info["video"] = local_path(info["filename"], command)
    return info


def download_youtube(url: str, command: list[str], log=print, folder: Path | None = None, name: str | None = None,
                     cancelled=lambda: False, extra: list[str] = (), on_line=None, keep_partial=lambda: False) -> Path:
    """Downloads the video with yt-dlp (in WSL by default): format and MP4 merge come from the yt-dlp config
    (~/.config/yt-dlp/config); the output folder replaces its own if `folder` is given. A video already there is
    not downloaded again. The audio is then extracted from the MP4 by ffmpeg, as for a file.
    `cancelled` (a Cancellation tells at once, see cancellation.py): when it turns true, yt-dlp is killed, its partial files are deleted
    and DownloadCancelled is raised. `extra`: yt-dlp options of the tracks chosen (see tracks.download_options).
    `on_line(line)`: each line yt-dlp writes (one per progress update), instead of the console.
    `keep_partial()`, asked when it is stopped: true for a pause, the partial files are kept and yt-dlp resumes from
    them when the same download is started again (DownloadCancelled.partial says which video they belong to)."""
    name = name or name_template(folder)
    info = youtube_info(url, command, folder, name)
    video = info["video"]
    if video.exists():
        log(i18n.t("log.already_downloaded", path=video))
        return video
    if cancelled():
        raise DownloadCancelled(i18n.t("err.cancelled"))
    log(i18n.t("log.downloading", title=info.get("title"), s=info.get("duration") or "?", path=video))
    args = [*_command(command), "--no-playlist", *(["--newline"] if on_line else []), *_output_options(command, folder, name), *extra, url]
    too_long = []

    def watched(line):                                     # the reason of a failure the user can act on, said plainly
        if _TOO_LONG.search(line):
            too_long.append(line)
        on_line(line)

    code = _run_cancellable(args, cancelled, watched) if on_line else _run_cancellable(args, cancelled)
    if code is None:
        stopped = DownloadCancelled(i18n.t("err.cancelled"))
        if keep_partial():
            stopped.partial = video
            log(i18n.t("log.download_paused"))
        else:
            removed = remove_partial_files(video)
            log(i18n.t("log.download_stopped", n=len(removed)))
        raise stopped
    if code != 0:
        raise SourceError(i18n.t("err.name_too_long") if too_long else i18n.t("err.ytdlp_failed", code=code))
    if not video.exists():
        raise SourceError(i18n.t("err.download_missing", path=video))
    return video


CHECK_S = 0.5                                  # how often a plain `cancelled` callable is asked (a Cancellation tells at once)
# Streams yt-dlp downloads apart before merging them (<name>.f137.mp4): video and audio only, so that a subtitle
# such as <name>.fr.srt is never taken for one.
STREAM_EXTENSIONS = ("mp4", "m4a", "webm", "mkv", "mov", "flv", "3gp", "ts", "opus", "ogg", "mp3", "aac", "weba")
THUMBNAILS = {".webp", ".jpg", ".jpeg", ".png"}


def _run_cancellable(args: list[str], cancelled, on_line=None) -> int | None:
    """Runs a command (its output goes to the console, as with subprocess.run, or line by line to `on_line`) and
    returns its exit code, or None when `cancelled()` turned true first: the command was killed with its child
    processes (ffmpeg merging the streams). yt-dlp in WSL is killed through wsl.exe, which ends the Linux process too."""
    if on_line is None:
        p = subprocess.Popen(args)
        reader = None
    else:
        p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                             env={**os.environ, "PYTHONIOENCODING": "utf-8"})     # yt-dlp.exe writes UTF-8 to the pipe
        reader = threading.Thread(target=_read_lines, args=(p.stdout, on_line), daemon=True)
        reader.start()
    killed = []

    def kill():                                # told the moment the cancellation is asked
        killed.append(True)
        _kill_tree(p)

    forget = cancellation.listen(cancelled, kill, CHECK_S)
    try:
        code = p.wait()
    finally:
        forget()
    if reader:
        reader.join(timeout=5)
    return None if killed and code != 0 else code      # ended by itself just before: its own result


def _read_lines(stream, on_line):
    for raw in iter(stream.readline, b""):
        try:
            on_line(raw.decode("utf-8", "replace"))
        except Exception:                                   # noqa: BLE001 (a display problem never stops a download)
            pass


def _kill_tree(p: subprocess.Popen):
    if os.name == "nt":                                    # kill() would leave ffmpeg, child of yt-dlp.exe, running
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)
    else:
        p.kill()
    try:
        p.wait(timeout=10)
    except subprocess.TimeoutExpired:
        p.kill()
        p.wait()


def remove_partial_files(video: Path, thumbnails: bool = True) -> list[Path]:
    """Deletes what an interrupted yt-dlp leaves next to the video it was making: `<name>.mp4.part`, the streams
    not merged yet (`<name>.f137.mp4`, `<name>.f140.m4a` and their `.part`), fragments and `.ytdl` state files.
    The finished video, and any other file of the folder, are kept. Returns the deleted files.
    `thumbnails` false: an image of the same name is kept (once the download is finished, it may be wanted)."""
    stem, removed = video.stem, []
    stream = re.compile(re.escape(stem) + r"\.(f[\w-]+|temp)\.(" + "|".join(STREAM_EXTENSIONS) + r")(\.part)?$", re.I)
    for p in video.parent.glob(glob_escape(stem) + ".*"):
        n = p.name
        partial = (n.endswith((".part", ".ytdl")) or ".part-Frag" in n or stream.match(n)
                   or thumbnails and p.suffix.lower() in THUMBNAILS and p.stem == stem)   # written first, to embed at the end
        if partial and p != video and p.is_file():
            for _ in range(10):                            # Windows: the killed process may hold the file a moment
                try:
                    p.unlink()
                    removed.append(p)
                    break
                except PermissionError:
                    time.sleep(0.3)
                except FileNotFoundError:
                    break
    return removed


def glob_escape(s: str) -> str:
    return re.sub(r"([\[\]*?])", r"[\1]", s)


# --- Playlists: downloaded into a folder of their own, which then becomes a project like any folder -----------------

PLAYLIST_TIMEOUT_S = 300


def _flat(url: str, command: list[str], *options: str) -> dict:
    r = subprocess.run([*_command(command), "--flat-playlist", *options, "-J", url], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=PLAYLIST_TIMEOUT_S)
    if r.returncode != 0:
        raise SourceError(f"yt-dlp : {(r.stderr or r.stdout).strip()[-500:]}")
    return json.loads(r.stdout)


def playlist_info(url: str, command: list[str]) -> dict | None:
    """{id, title, entries: [{url, title, duration}], with_video} if the address is a playlist (or a
    channel), None otherwise. Read without downloading anything. `with_video`: the address also designates
    one video of the list (youtube.com/watch?v=…&list=…), which can be processed alone."""
    d = _flat(url, command, "--yes-playlist")
    if d.get("_type") != "playlist":
        return None
    entries = _entries(d)
    if not entries:
        return None
    alone = _flat(url, command, "--no-playlist")
    return {"id": d.get("id"), "title": d.get("title") or d.get("id") or "playlist", "entries": entries,
            "with_video": alone.get("_type") != "playlist"}


def _entries(d: dict) -> list[dict]:
    return [{"url": e.get("url") or e.get("webpage_url"), "title": e.get("title") or e.get("id") or "?", "duration": e.get("duration")}
            for e in d.get("entries") or [] if e and (e.get("url") or e.get("webpage_url"))]


def address_lines(text: str) -> list[str]:
    """Addresses of a list: one per line; blank lines and lines starting with # are ignored, repeats kept once."""
    lines = (line.strip() for line in text.lstrip("﻿").splitlines())
    return list(dict.fromkeys(s for s in lines if s and not s.startswith("#")))


def read_addresses(path: str | Path) -> list[str]:
    """Addresses of a text file, one per line (UTF-8, with or without BOM)."""
    return address_lines(Path(path).read_text(encoding="utf-8-sig", errors="replace"))


def not_urls(addresses: list[str]) -> list[str]:
    return [a for a in addresses if not is_url(a)]


def is_address_file(s: str) -> bool:
    return s.lower().endswith(".txt") and Path(s).is_file()


def list_title(when: float | None = None) -> str:
    """Folder name of a list of addresses typed or pasted (a file gives its own name)."""
    return i18n.t("list.title", date=time.strftime("%Y-%m-%d %H-%M", time.localtime(when)))


def describe(url: str, command: list[str]) -> dict:
    """What an address designates, without downloading: {playlist: its title, or None for a single video, entries:
    [{url, title, duration}]}. The address of a video inside a playlist (watch?v=…&list=…) is that video alone."""
    d = _flat(url, command, "--no-playlist")
    if d.get("_type") == "playlist":
        return {"playlist": d.get("title") or d.get("id") or "playlist", "entries": _entries(d)}
    return {"playlist": None, "entries": [{"url": d.get("webpage_url") or url, "title": d.get("title") or url, "duration": d.get("duration")}]}


# Sites that give the title of a page at once (oEmbed: a few hundredths of a second, where yt-dlp takes seconds):
# shown while yt-dlp reads the details.
OEMBED = {r"(^|\.)(youtube\.com|youtu\.be)$": "https://www.youtube.com/oembed?format=json&url={}",
          r"(^|\.)(dailymotion\.com|dai\.ly)$": "https://www.dailymotion.com/services/oembed?format=json&url={}",
          r"(^|\.)vimeo\.com$": "https://vimeo.com/api/oembed.json?url={}"}
OEMBED_TIMEOUT_S = 4


def quick_title(url: str) -> str | None:
    """Title of a video address in a fraction of a second, for the sites that offer it (oEmbed); None otherwise
    (other site, playlist, network error): the details read by yt-dlp will give it."""
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    endpoint = next((e for pattern, e in OEMBED.items() if re.search(pattern, host)), None)
    if endpoint is None:
        return None
    try:
        with urllib.request.urlopen(endpoint.format(urllib.parse.quote(url, safe="")), timeout=OEMBED_TIMEOUT_S) as r:
            return (json.loads(r.read().decode("utf-8")).get("title") or "").strip() or None
    except (OSError, ValueError):
        return None


def list_info(urls: list[str], command: list[str], title: str) -> dict:
    """A list of addresses read as one playlist (same form as playlist_info): each address is one video, or the
    videos of a playlist in their order. An address that cannot be read stays in the list: its download fails
    and is reported like a private video of a playlist."""
    entries = []
    for u in urls:
        try:
            entries += describe(u, command)["entries"]
        except (SourceError, subprocess.SubprocessError, ValueError):
            entries.append({"url": u, "title": u, "duration": None})
    return {"id": None, "title": title, "entries": entries, "with_video": False}


def folder_name(title: str, max_len: int = 100) -> str:
    """Readable folder name (accents and spaces kept), without the characters Windows refuses."""
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " ", title)
    s = re.sub(r"\s+", " ", s).strip(" .")
    return s[:max_len].rstrip(" .") or "playlist"


def playlist_folder(output_dir: str | Path, info: dict) -> Path:
    return Path(output_dir) / folder_name(info["title"])


def download_playlist(info: dict, command: list[str], folder: Path, log=print, cancelled=lambda: False,
                      progress=lambda done, total, title: None) -> tuple[list[Path], list[dict]]:
    """Downloads the videos one by one into `folder`, numbered in the order of the list ("001 - title"). A video that
    fails (private, removed) is skipped; one already there is not downloaded again, so running it again resumes.
    Returns (files, failures [{title, url, error}])."""
    folder.mkdir(parents=True, exist_ok=True)
    entries = info["entries"]
    width = max(3, len(str(len(entries))))
    files, failures = [], []
    for i, e in enumerate(entries, 1):
        if cancelled():
            break
        progress(i - 1, len(entries), e["title"])
        log(i18n.t("log.playlist_video", i=i, n=len(entries), title=e["title"]))
        try:
            files.append(download_youtube(e["url"], command, log, folder, name_template(folder, f"{i:0{width}d} - "), cancelled))
        except DownloadCancelled:
            break
        except (SourceError, OSError, subprocess.SubprocessError, ValueError) as err:
            log(i18n.t("log.playlist_video_failed", title=e["title"], error=err))
            failures.append({"title": e["title"], "url": e["url"], "error": str(err)})
    progress(len(files) + len(failures), len(entries), "")
    return files, failures
