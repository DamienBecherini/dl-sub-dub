"""What a video file holds, for the Information tab of its line: the picture (definition, codec, frame rate), the audio
tracks and the subtitles inside it, the subtitle files next to it, its size and duration; and a thumbnail, a picture
taken by ffmpeg at a tenth of the video, kept in a cache of the user folder.

Read with ffprobe (a second at most); remembered while the file keeps the same date and size.
"""
import hashlib
import json
import subprocess
import threading
from pathlib import Path

from . import config, folder, source

_cache: dict = {}
_lock = threading.Lock()
THUMB_WIDTH = 480


class MediaError(Exception):
    pass


def _key(video: Path) -> tuple:
    st = video.stat()
    return str(video), st.st_mtime_ns, st.st_size


def _fps(rate: str | None) -> float | None:
    try:
        a, b = (rate or "0/0").split("/")
        return round(int(a) / int(b), 2) if int(b) else None
    except ValueError:
        return None


def info(video: Path, run=subprocess.run) -> dict:
    """{name, path, size, duration, source (the address it was downloaded from, written by yt-dlp; "" if unknown),
    video {width, height, codec, fps}, audio [{language, codec, channels, title, default}], subtitles [{language, codec,
    title}], sidecar [{language, name}]}."""
    video = Path(video)
    if not video.is_file():
        raise MediaError(str(video))
    key = _key(video)
    with _lock:
        if key in _cache:
            return _cache[key]
    r = run([source.tool("ffprobe"), "-v", "error", "-show_entries",
             "format=duration:format_tags:stream=index,codec_type,codec_name,width,height,avg_frame_rate,channels"
             ":stream_tags=language,title:stream_disposition=default,attached_pic", "-of", "json", str(video)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    if r.returncode:
        raise MediaError(r.stderr.strip()[:300])
    d = json.loads(r.stdout or "{}")
    streams = d.get("streams") or []
    tag = lambda s, k: ((s.get("tags") or {}).get(k) or "")  # noqa: E731
    pictures = [s for s in streams if s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic")]
    v = pictures[0] if pictures else {}
    sidecar = []
    for f in sorted(video.parent.glob(glob_escape(video.stem) + "*")):
        code = folder.subtitle_language_code(video.stem, f)
        if code and f.is_file():
            sidecar.append({"language": code, "name": f.name})
    result = {
        "name": video.name, "path": str(video), "size": key[2],
        "duration": float((d.get("format") or {}).get("duration") or 0) or None,
        "source": source_address((d.get("format") or {}).get("tags") or {}),
        "video": {"width": v.get("width"), "height": v.get("height"), "codec": v.get("codec_name"),
                  "fps": _fps(v.get("avg_frame_rate"))} if v else None,
        "audio": [{"language": tag(s, "language"), "codec": s.get("codec_name"), "channels": s.get("channels"),
                   "title": tag(s, "title"), "default": bool((s.get("disposition") or {}).get("default"))}
                  for s in streams if s.get("codec_type") == "audio"],
        "subtitles": [{"language": tag(s, "language"), "codec": s.get("codec_name"), "title": tag(s, "title")}
                      for s in streams if s.get("codec_type") == "subtitle"],
        "sidecar": sidecar,
    }
    with _lock:
        _cache[key] = result
    return result


def source_address(tags: dict) -> str:
    """The address of the page a video was downloaded from: yt-dlp writes it in "purl" or "comment" (case varies with
    the container)."""
    low = {str(k).lower(): str(v) for k, v in tags.items()}
    for key in ("purl", "comment", "url"):
        value = low.get(key, "").strip()
        if value.startswith(("http://", "https://")) and " " not in value:
            return value
    return ""


def glob_escape(text: str) -> str:
    """A file name in a glob pattern: [ and ] taken literally (titles of videos often hold them)."""
    return "".join(f"[{c}]" if c in "[]*?" else c for c in text)


def thumbnail(video: Path, duration: float | None = None, run=subprocess.run) -> Path:
    """A JPEG of the video at a tenth of its duration (at most the first minute), made once and kept."""
    video = Path(video)
    if not video.is_file():
        raise MediaError(str(video))
    name = hashlib.sha1("|".join(map(str, _key(video))).encode("utf-8")).hexdigest()[:20] + ".jpg"
    out = config.state_dir() / "thumbnails" / name
    if out.is_file():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    at = min(60.0, (duration or 0) / 10)
    partial = out.with_suffix(".part.jpg")
    r = run([source.tool("ffmpeg"), "-loglevel", "error", "-y", "-ss", f"{at:.2f}", "-i", str(video), "-frames:v", "1",
             "-vf", f"scale={THUMB_WIDTH}:-2", "-q:v", "5", str(partial)], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=60)
    if r.returncode or not partial.is_file():
        partial.unlink(missing_ok=True)
        raise MediaError(r.stderr.strip()[:300] or "ffmpeg")
    partial.replace(out)
    return out
