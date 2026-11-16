"""Creates a `<name>.multi.mkv` next to the video: video and original tracks copied without re-encoding, dubbed track
and subtitles added.

The original video is never modified. The file appears under its final name only once checked.
"""
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import folder, i18n, source, space, steps

_CODES3: dict[str, str] = {}
for _three, _two in folder.LANGUAGE_TAGS.items():
    _CODES3.setdefault(_two, _three)                   # the first form is the bibliographic one (fre, ger), the one of Matroska
_TEXT_TO_CONVERT = {"mov_text"}                        # subtitles of an .mp4: Matroska cannot copy them as they are
MAX_GAP_S = 2.0


class MuxError(source.SourceError):
    pass


def code3(code: str) -> str:
    return _CODES3.get(code, "und")


# The name of each language in that language: a track is named for the people who will choose it.
NATIVE = {"ar": "العربية", "br": "Brezhoneg", "ca": "Català", "cs": "Čeština", "da": "Dansk", "de": "Deutsch", "el": "Ελληνικά",
          "en": "English", "eo": "Esperanto", "es": "Español", "eu": "Euskara", "fi": "Suomi", "fr": "Français", "he": "עברית",
          "hi": "हिन्दी", "hu": "Magyar", "it": "Italiano", "ja": "日本語", "ko": "한국어", "nl": "Nederlands", "no": "Norsk",
          "oc": "Occitan", "pl": "Polski", "pt": "Português", "ro": "Română", "ru": "Русский", "sv": "Svenska", "tr": "Türkçe",
          "uk": "Українська", "zh": "中文"}
DUB_ENGINE = "OmniVoice"                       # the voice engine the dubbing program runs: said in the title of its tracks


def title(code: str) -> str:
    """The name of a language in that language ("English", "Français"); failing that, in the active one."""
    base = str(code).lower().split("-")[0]
    return NATIVE.get(base) or i18n.language_name(code)[:1].upper() + i18n.language_name(code)[1:]


def dub_title(code: str) -> str:
    """"English (AI dub: OmniVoice, dl-sub-dub)": in the language of the track when the tool has it, in English otherwise."""
    return i18n.text_in(str(code).lower().split("-")[0], "mkv.dub_track", lang=title(code), engine=DUB_ENGINE)


def subtitle_title(code: str, spoken: str) -> str:
    """"Français" for the spoken language, "English (AI translation)" for a translation."""
    base = str(code).lower().split("-")[0]
    return title(code) if base == str(spoken).lower().split("-")[0] else i18n.text_in(base, "mkv.sub_translated", lang=title(code))


def probe(path: Path) -> dict:
    r = subprocess.run([source.tool("ffprobe"), "-v", "error", "-show_entries",
                        "stream=index,codec_type,codec_name:stream_tags=language,title:stream_disposition=attached_pic:format=duration",
                        "-of", "json", str(path)], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode:
        raise MuxError(i18n.t("err.probe_failed", file=Path(path).name, detail=r.stderr[:300]))
    info = json.loads(r.stdout)
    # A picture stored as a video track of one frame (an MKV made by a tool that copied the cover as a track, as this
    # one did before): a cover too, next to a real video track. Copied as a track, it breaks the interleaving.
    videos = [s for s in info.get("streams", []) if s.get("codec_type") == "video"]
    if any(s.get("codec_name") not in COVER_TYPES for s in videos):
        for s in videos:
            if s.get("codec_name") in COVER_TYPES:
                s.setdefault("disposition", {})["attached_pic"] = 1
    return info


def is_cover(stream: dict) -> bool:
    """A picture attached to the video (the thumbnail yt-dlp embeds), not a video track (see probe)."""
    return stream.get("codec_type") == "video" and (stream.get("disposition") or {}).get("attached_pic") == 1


COVER_TYPES = {"png": (".png", "image/png"), "mjpeg": (".jpg", "image/jpeg"), "jpeg": (".jpg", "image/jpeg"),
               "webp": (".webp", "image/webp")}


def cover_of(video: Path, info: dict, folder: Path) -> tuple[Path, str] | None:
    """The cover of the video written to a file of `folder` (to attach it to the MKV), with its type; None without one,
    or if it cannot be read (the MKV is made without it)."""
    cover = next((s for s in info["streams"] if is_cover(s) and s.get("codec_name") in COVER_TYPES), None)
    if cover is None:
        return None
    ext, mime = COVER_TYPES[cover["codec_name"]]
    path = Path(folder) / f"cover{ext}"
    r = subprocess.run([source.tool("ffmpeg"), "-loglevel", "error", "-y", "-i", str(video), "-map", f"0:{cover['index']}",
                        "-c", "copy", "-frames:v", "1", "-f", "image2", str(path)], capture_output=True)
    return (path, mime) if r.returncode == 0 and path.is_file() else None


def voices_of(dubbed, target: str) -> list[tuple[str, Path]]:
    """The dubbed voices: [(code, audio file)], or one file in the language `target`."""
    return [(c, Path(p)) for c, p in dubbed] if isinstance(dubbed, (list, tuple)) else [(target, Path(dubbed))]


def command(ffmpeg: str, video: Path, info: dict, dubbed, target: str, spoken: str, subtitles: list[tuple[str, Path]], output: Path,
            cover: tuple[Path, str] | None = None) -> list[str]:
    """ffmpeg options. `dubbed`: one audio file in `target`, or [(code, audio file)] (one track each); `subtitles`:
    [(code, .srt file)]; `info`: result of `probe(video)`; `cover`: (picture, type) attached to the MKV.
    The video tracks only, one by one, never the cover: copied as a track of one frame, it makes ffmpeg write the added
    tracks at the end of the file, and VLC then plays them silent (no sound after switching the audio track, no
    subtitles)."""
    voices = voices_of(dubbed, target)
    audios = [s for s in info["streams"] if s["codec_type"] == "audio"]
    existing = [s for s in info["streams"] if s["codec_type"] == "subtitle"]
    n_a, n_s = len(audios), len(existing)
    cmd = [ffmpeg, "-loglevel", "error", "-y", "-i", str(video)]
    for _, f in voices:
        cmd += ["-i", str(f)]
    for _, f in subtitles:
        cmd += ["-i", str(f)]
    for n, v in enumerate(info["streams"]):
        if v["codec_type"] == "video" and not is_cover(v):
            cmd += ["-map", f"0:{v.get('index', n)}"]
    cmd += ["-map", "0:a", "-map", "0:s?"]
    for i in range(len(voices)):
        cmd += ["-map", f"{1 + i}:a:0"]
    for i in range(len(subtitles)):
        cmd += ["-map", f"{1 + len(voices) + i}:0"]
    cmd += ["-c:v", "copy", "-c:a", "copy", "-c:s", "copy"]
    for i, s in enumerate(existing):
        if s.get("codec_name") in _TEXT_TO_CONVERT:
            cmd += [f"-c:s:{i}", "srt"]
    for i in range(len(subtitles)):
        cmd += [f"-c:s:{n_s + i}", "srt"]
    for i, a in enumerate(audios):
        tags = a.get("tags") or {}
        if tags.get("language", "und") == "und":
            cmd += [f"-metadata:s:a:{i}", f"language={code3(spoken)}"]
        if not tags.get("title"):
            cmd += [f"-metadata:s:a:{i}", f"title={title(spoken)} (original)"]
        cmd += [f"-disposition:a:{i}", "default" if i == 0 else "0"]
    for i, (code, _) in enumerate(voices):
        cmd += [f"-metadata:s:a:{n_a + i}", f"language={code3(code)}", f"-metadata:s:a:{n_a + i}",
                f"title={dub_title(code)}", f"-disposition:a:{n_a + i}", "0"]
    for i, (code, _) in enumerate(subtitles):
        cmd += [f"-metadata:s:s:{n_s + i}", f"language={code3(code)}", f"-metadata:s:s:{n_s + i}", f"title={subtitle_title(code, spoken)}", f"-disposition:s:{n_s + i}", "0"]
    if cover:
        cmd += ["-attach", str(cover[0]), "-metadata:s:t:0", f"mimetype={cover[1]}",
                "-metadata:s:t:0", f"filename=cover{Path(cover[0]).suffix}"]      # "cover.*": the cover of Matroska
    return cmd + ["-f", "matroska", str(output)]


def verify(mkv: Path, info: dict, added: int):
    """The MKV must hold the original tracks copied (video, audio, subtitles: a data stream, such as the one some
    downloads carry, is not copied) plus the added ones, and last as long as the video."""
    copied = [s for s in info["streams"] if s.get("codec_type") in ("video", "audio", "subtitle") and not is_cover(s)]
    done, expected = probe(mkv), len(copied) + added
    tracks = [s for s in done["streams"] if s.get("codec_type") != "attachment" and not is_cover(s)]   # its cover apart
    #   (ffprobe shows the picture attached to a Matroska file as an attached picture)
    gap = abs(float(done["format"]["duration"]) - float(info["format"]["duration"]))
    if len(tracks) != expected or gap > MAX_GAP_S:
        raise MuxError(i18n.t("err.mkv_incomplete", found=len(tracks), expected=expected, gap=f"{gap:.1f}"))


def to_trash(file: Path) -> bool:
    """Sends a file to the recycle bin; False if it is not possible (the file then stays in place)."""
    file = Path(file)
    try:
        if os.name == "nt":                    # path passed through the environment: no quotes or accents to escape
            script = ("Add-Type -AssemblyName Microsoft.VisualBasic; [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile("
                      "$env:TRASH_FILE, 'OnlyErrorDialogs', 'SendToRecycleBin')")
            subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], env={**os.environ, "TRASH_FILE": str(file)},
                           capture_output=True, timeout=600, check=True)
        elif shutil.which("gio"):
            subprocess.run(["gio", "trash", str(file)], capture_output=True, timeout=600, check=True)
        else:
            return False
    except (OSError, subprocess.SubprocessError):
        return False
    return not file.exists()


def replace_original(video: Path, mkv: Path, added: int, log=print) -> Path:
    """Replaces the video by the MKV, only if it is checked and if the original did go to the recycle bin. Returns the
    final file. The subtitles next to it keep the same base name: only the extension of the video can change."""
    video, mkv = Path(video), Path(mkv)
    final = video.with_suffix(".mkv")
    if final != video and final.exists():
        log(i18n.t("log.replace_exists", file=final.name))
        return mkv
    verify(mkv, probe(video), added)
    if not to_trash(video):
        log(i18n.t("log.trash_unavailable"))
        return mkv
    mkv.replace(final)
    log(i18n.t("log.replaced", file=final.name))
    return final


def mux(video: Path, dubbed, target: str, spoken: str, subtitles: list[tuple[str, Path]], log=print, redo: bool = False) -> Path:
    """`dubbed`: one audio file in `target`, or [(code, audio file)]."""
    voices = voices_of(dubbed, target)
    video = Path(video)
    output = steps.mkv_path(video)
    if output.exists() and not redo:
        log(i18n.t("log.mkv_already", file=output.name))
        return output
    partial = output.with_name(output.name + ".part")
    space.check(output.parent, space.files_size(video, *(p for _c, p in voices), *(p for _c, p in subtitles)))     # the MKV holds them all
    info = probe(video)
    with tempfile.TemporaryDirectory(prefix="dsd-cover-") as tmp:
        cover = cover_of(video, info, Path(tmp))
        try:
            r = subprocess.run(command(source.tool("ffmpeg"), video, info, dubbed, target, spoken, subtitles, partial, cover),
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
            if r.returncode:
                raise MuxError(f"ffmpeg : {r.stderr[-400:]}")
            verify(partial, info, len(voices) + len(subtitles))
            partial.replace(output)
        finally:
            partial.unlink(missing_ok=True)
    log(i18n.t("log.mkv_done", path=output, mb=f"{output.stat().st_size / 2**20:.0f}"))
    return output
