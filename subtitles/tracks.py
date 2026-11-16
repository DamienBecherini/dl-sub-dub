"""What a site offers for a video, and the tracks chosen for its download: audio tracks (the original, dubbed ones,
dubbed by AI), subtitles of the site (written by hand, or automatic in the spoken language), and the ones added
after the download without downloading the video again.

The information comes from `yt-dlp -J` (several seconds per video): only a short summary is kept in the project.
The kind of an audio track is read from its note on the site ("original", "dubbed", "dubbed-auto"…), as yt-dlp
gives it; a track without such a word only shows its language.
The subtitles of the site are written next to the video as `<name>.site.<language>.srt`, apart from the ones this
tool makes (`<name>.<language>.srt`), which they never replace.
"""
import json
import re
import subprocess
from pathlib import Path

from . import i18n, muxing, source

INFO_TIMEOUT_S = 180
SITE_SUFFIX = ".site"                                  # <name>.site.<language>.srt
# A track alone: the yt-dlp configuration may embed or write the picture of the video (impossible in an audio file,
# and yt-dlp then fails): not for these downloads.
TRACK_ONLY = ["--no-embed-thumbnail", "--no-write-thumbnail"]
AUDIO_FILES = {".m4a", ".webm", ".opus", ".ogg", ".mp3", ".aac", ".mka"}
MIN_HEIGHT = 144
# The language of a track is given by the site of the video, and it names a file ("<video>.audio-<language>.m4a") and
# goes into options of yt-dlp: only a plain code is taken ("fr", "en-US", "zh-Hans", "es-419"). Anything else, such as
# "../../x", would write the track outside the folder.
_LANGUAGE = re.compile(r"[A-Za-z0-9]+(?:[-_][A-Za-z0-9]+)*")
LANGUAGE_MAX = 35


def is_language(code) -> bool:
    return isinstance(code, str) and len(code) <= LANGUAGE_MAX and _LANGUAGE.fullmatch(code) is not None


def checked(code) -> str:
    """The language code of a track, refused if it is not a plain code."""
    if not is_language(code):
        raise source.SourceError(i18n.t("err.track_language", code=str(code)[:60]))
    return code


def track_kind(note: str) -> str:
    n = (note or "").lower()
    if "original" in n:
        return "original"
    if re.search(r"dubbed[- ]auto|auto[- ]dubbed|\bai\b", n):
        return "auto"
    if "dubbed" in n:
        return "dubbed"
    if "descriptive" in n:
        return "descriptive"
    return ""


def summarize(d: dict) -> dict:
    """The information of `yt-dlp -J` the page shows: title, author, date, picture, heights of the image, audio
    tracks, subtitles (by hand, and automatic in the spoken language)."""
    by_language = {}                                   # one track per language; its kind from any of its formats
    for f in d.get("formats") or []:
        if f.get("vcodec") not in (None, "none") or not is_language(f.get("language")):     # not a plain code: not offered
            continue
        t = by_language.setdefault(f["language"], {"language": f["language"], "kind": ""})
        t["kind"] = t["kind"] or track_kind(f.get("format_note"))
    audio = list(by_language.values())
    if len(audio) == 1 and not audio[0]["kind"]:
        audio[0]["kind"] = "original"
    audio.sort(key=lambda a: a["kind"] != "original")
    auto = sorted(k.removesuffix("-orig") for k in (d.get("automatic_captions") or {}) if k.endswith("-orig"))
    if not auto and d.get("language") and d["language"] in (d.get("automatic_captions") or {}):
        auto = [d["language"]]
    auto = [k for k in auto if is_language(k)]
    written = sorted(k for k in (d.get("subtitles") or {}).keys() - {"live_chat"} if is_language(k))
    return {"title": d.get("title") or "", "duration": d.get("duration"), "uploader": d.get("uploader") or d.get("channel") or "",
            "upload_date": d.get("upload_date") or "", "thumbnail": d.get("thumbnail") or "", "language": d.get("language") or "",
            "heights": sorted({f["height"] for f in d.get("formats") or [] if (f.get("height") or 0) >= MIN_HEIGHT}, reverse=True),
            "audio": audio, "subtitles": written, "auto_subtitles": auto}


def site_info(url: str, command: list[str]) -> dict:
    r = subprocess.run([*source._command(command), "--no-playlist", "-J", url], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=INFO_TIMEOUT_S)
    if r.returncode != 0:
        raise source.SourceError(f"yt-dlp : {(r.stderr or r.stdout).strip()[-500:]}")
    return summarize(json.loads(r.stdout))


def format_choice(max_height: int, audio: list[str]) -> str:
    """yt-dlp -f for the image up to `max_height` and these audio tracks (by language, the first one first)."""
    image = f"bv*[height<={max_height}]" if max_height else "bv*"
    tracks = "+".join(f"ba[language={checked(lang)}]" for lang in audio)
    return "/".join(dict.fromkeys([f"{image}+{tracks}", f"bv*+{tracks}"]))


def subtitle_options(command: list[str], folder: Path, name: str, subtitles: list[dict]) -> list[str]:
    """yt-dlp options writing the subtitles of the site ([{language, auto}]) next to the video, as .srt."""
    if not subtitles:
        return []
    langs = [checked(s["language"]) + ("-orig" if s.get("auto") else "") for s in subtitles]
    options = ["--sub-langs", ",".join(langs), "--convert-subs", "srt",
               "-o", "subtitle:" + source.remote_path(folder, command) + f"/{name}{SITE_SUFFIX}.%(ext)s"]
    if any(not s.get("auto") for s in subtitles):
        options.append("--write-subs")
    if any(s.get("auto") for s in subtitles):
        options.append("--write-auto-subs")
    return options


def download_options(command: list[str], folder: Path, name: str, max_height: int, audio: list[str] | None,
                     subtitles: list[dict] | None) -> list[str]:
    """What the download adds to the yt-dlp command for the tracks chosen: several audio tracks (otherwise the
    site's default one, the original), and the subtitles of the site."""
    options = []
    if audio:
        options += ["-f", format_choice(max_height, audio)] + (["--audio-multistreams"] if len(audio) > 1 else [])
    return options + subtitle_options(command, folder, name, subtitles or [])


def fetch_subtitles(url: str, command: list[str], folder: Path, name: str, subtitles: list[dict], log=print, cancelled=lambda: False):
    """Subtitles of the site added after the download, without the video."""
    args = [*source._command(command), "--no-playlist", "--skip-download", *TRACK_ONLY, *subtitle_options(command, folder, name, subtitles), url]
    code = source._run_cancellable(args, cancelled)
    if code is None:
        raise source.DownloadCancelled(i18n.t("err.cancelled"))
    if code != 0:
        raise source.SourceError(i18n.t("err.ytdlp_failed", code=code))


def add_audio(url: str, command: list[str], video: Path, language: str, log=print, cancelled=lambda: False) -> Path:
    """An audio track of the site added to a video already downloaded: only that track is downloaded, then copied
    into the video with ffmpeg (no re-encoding), as its last track, with its language. The video keeps its name."""
    video = Path(video)
    stem = f"{video.stem}.audio-{checked(language)}"
    args = [*source._command(command), "--no-playlist", *TRACK_ONLY, "-f", f"ba[language={language}][ext=m4a]/ba[language={language}]",
            "-o", source.remote_path(video.parent, command) + f"/{stem}.%(ext)s", url]
    code = source._run_cancellable(args, cancelled)
    if code is None:
        source.remove_partial_files(video.with_name(stem + ".m4a"))
        raise source.DownloadCancelled(i18n.t("err.cancelled"))
    if code != 0:
        raise source.SourceError(i18n.t("err.ytdlp_failed", code=code))
    leftovers = list(video.parent.glob(source.glob_escape(stem) + ".*"))
    track = next((p for p in leftovers if p.suffix.lower() in AUDIO_FILES), None)
    for p in leftovers:                                # a picture written all the same, a second copy…
        if p != track:
            p.unlink(missing_ok=True)
    if track is None:
        raise source.SourceError(i18n.t("err.download_missing", path=video.with_name(stem)))
    count = len(json.loads(subprocess.run([source.tool("ffprobe"), "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
                                           "-of", "json", str(video)], capture_output=True, text=True).stdout or "{}").get("streams") or [])
    out = video.with_name(video.stem + ".tracks" + video.suffix)
    r = subprocess.run([source.tool("ffmpeg"), "-y", "-nostdin", "-loglevel", "error", "-i", str(video), "-i", str(track), "-map", "0",
                        "-map", "1:a", "-c", "copy", f"-metadata:s:a:{count}",
                        f"language={muxing.code3(language.split('-')[0].lower())}", str(out)],     # MP4: 3 letters (ger)
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        out.unlink(missing_ok=True)
        raise source.SourceError(f"ffmpeg : {r.stderr.strip()[-400:]}")
    out.replace(video)
    track.unlink(missing_ok=True)
    log(i18n.t("log.audio_added", language=language, path=video))
    return video


def site_subtitle_files(video: Path) -> list[Path]:
    """The subtitles of the site next to a video."""
    video = Path(video)
    return sorted(video.parent.glob(source.glob_escape(video.stem + SITE_SUFFIX) + ".*.srt"))
