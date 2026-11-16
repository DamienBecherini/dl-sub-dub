"""Where a download stands, read from the lines of yt-dlp (run with --newline: one line per progress update).

yt-dlp says which formats it takes (`[info] …: Downloading 1 format(s): 399+251`: the video, then each audio track,
or a single format holding both), then downloads each stream in that order (`[download] Destination: …` then
`[download]  42.0% of 50.00MiB at 3.20MiB/s ETA 00:10`), the subtitles of the site (`Writing video subtitles`), and
ends with the post-processing: merging the streams (`[Merger]`), the metadata, the subtitles and the thumbnail
embedded. The page draws one segment per part (video, audio…, subtitles, finishing) and the step under way.

The log keeps the lines as a terminal would show them: a progress line replaces the one before it.
"""
import re
import threading
import time
from pathlib import Path

LOG_LINES = 200
SUBTITLE_EXTENSIONS = (".vtt", ".srt", ".ass", ".ttml", ".srv3", ".json3")
THUMBNAIL_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")

_FORMATS = re.compile(r"^\[info\] [^:]+: Downloading \d+ format\(s\): (\S+)")
_DESTINATION = re.compile(r"^\[download\] Destination: (.+)$")
_PROGRESS = re.compile(r"^\[download\]\s+(?P<percent>[\d.]+)% of\s+~?\s*(?P<size>\S+)"
                       r"(?:\s+at\s+(?P<speed>\S+))?(?:\s+ETA\s+(?P<eta>\S+))?(?:\s+in\s+(?P<took>\S+))?")
_SUBTITLES = re.compile(r"^\[info\] Writing video subtitles to: .*\.([\w-]+)\.\w+$")
_ALREADY = re.compile(r"^\[download\] .+ has already been downloaded")
_SLEEPING = re.compile(r"^\[download\] Sleeping [\d.]+ seconds")
_EXTRACTOR = re.compile(r"^\[(?!download\]|info\])[\w:]+\] ")
_FINISHING = {"[Merger]": "merge", "[VideoRemuxer]": "merge", "[VideoConvertor]": "merge", "[Metadata]": "metadata",
              "[EmbedSubtitle]": "embed_subtitles", "[ThumbnailsConvertor]": "thumbnail", "[EmbedThumbnail]": "thumbnail",
              "[FixupM3u8]": "fixup", "[FixupM4a]": "fixup", "[FixupStretched]": "fixup", "[FixupDuplicateMoov]": "fixup",
              "[SubtitlesConvertor]": "convert_subtitles"}


def _forms(folder) -> list[str]:
    """The ways yt-dlp writes a folder: D:/x/y/, with backslashes (Windows), /mnt/d/x/y/ (yt-dlp in WSL)."""
    p = Path(folder).as_posix().rstrip("/") + "/"
    forms = [p, p.replace("/", "\\")]
    if m := re.match(r"^([A-Za-z]):/(.*)$", p):
        forms.append(f"/mnt/{m.group(1).lower()}/{m.group(2)}")
    return forms


class DownloadProgress:
    def __init__(self, folder: str | Path | None = None):
        self.lock = threading.Lock()
        self.short = _forms(folder) if folder else []    # the folder of the project, written "…/" in the log
        self.parts: list[dict] = []            # [{kind: video|audio|both|subtitles, language?, percent, done}]
        self.current: int | None = None        # index of the part being downloaded
        self.phase = "starting"                # starting, video, audio, both, subtitles, merge, metadata, thumbnail…
        self.language = ""                     # of the subtitles being written
        self.speed = self.eta = self.size = ""
        self.finishing = False
        self.log: list[str] = []
        self._progress_line = False            # the last line of the log is a progress line

    # --- lines of yt-dlp ---
    def feed(self, line: str):
        line = line.rstrip("\r\n")
        if not line.strip():
            return
        with self.lock:
            progress = _PROGRESS.match(line)
            self._log(line, progress is not None)
            if progress:
                self._progress(progress)
            elif m := _FORMATS.match(line):
                ids = m.group(1).split("+")
                self.parts = [{"kind": "both" if len(ids) == 1 else ("video" if i == 0 else "audio"), "percent": 0.0, "done": False}
                              for i in range(len(ids))]
            elif m := _DESTINATION.match(line):
                self._destination(m.group(1))
            elif m := _SUBTITLES.match(line):
                self.phase, self.language = "subtitles", m.group(1)
            elif _ALREADY.match(line):
                self._end_part()
            elif _SLEEPING.match(line):
                self.phase = "waiting"
            elif self.phase in ("starting", "reading") and _EXTRACTOR.match(line):
                self.phase = "reading"            # [youtube] …: reading the page of the site
            else:
                for tag, phase in _FINISHING.items():
                    if line.startswith(tag):
                        self._end_part()
                        if phase != "convert_subtitles":
                            self.finishing, self.phase, self.speed, self.eta = True, phase, "", ""
                        break

    def note(self, line: str):
        """A line of the tool itself in the log."""
        with self.lock:
            self._log(line, False)

    def _log(self, line: str, progress: bool):
        for form in self.short:
            line = line.replace(form, "…/")
        stamped = time.strftime("[%H:%M:%S] ") + line
        if progress and self._progress_line:
            self.log[-1] = stamped
        else:
            self.log.append(stamped)
            del self.log[:-LOG_LINES]
        self._progress_line = progress

    def _destination(self, path: str):
        lower = path.lower()
        if lower.endswith(SUBTITLE_EXTENSIONS):
            m = re.search(r"\.([\w-]+)\.\w+$", path)
            self.phase, self.language, self.current = "subtitles", m.group(1) if m else "", None
            return
        if lower.endswith(THUMBNAIL_EXTENSIONS):
            return
        self._end_part()
        nxt = next((i for i, p in enumerate(self.parts) if not p["done"] and p["kind"] != "subtitles"), None)
        if nxt is None:                        # no formats line seen: one part more
            self.parts.append({"kind": "both" if not self.parts else "audio", "percent": 0.0, "done": False})
            nxt = len(self.parts) - 1
        self.current = nxt
        self.phase = self.parts[nxt]["kind"]

    def _progress(self, m):
        percent = min(100.0, float(m.group("percent")))
        self.size = m.group("size") or self.size
        self.speed = m.group("speed") or ""
        self.eta = m.group("eta") or ""
        if self.phase == "subtitles" or self.current is None:
            return
        part = self.parts[self.current]
        part["percent"] = percent
        if m.group("took") or percent >= 100:
            part["done"], part["percent"] = True, 100.0
            self.speed = self.eta = ""

    def _end_part(self):
        if self.current is not None and self.current < len(self.parts):
            self.parts[self.current].update(done=True, percent=100.0)
        self.current = None

    # --- for the page ---
    def to_dict(self) -> dict:
        with self.lock:
            part = self.parts[self.current] if self.current is not None else None
            return {"phase": self.phase, "language": self.language, "percent": part["percent"] if part else None,
                    "size": self.size if part else "", "speed": self.speed, "eta": self.eta,
                    "part": (self.current + 1) if self.current is not None else None,
                    "parts": [dict(p) for p in self.parts], "finishing": self.finishing}

    def lines(self) -> list[str]:
        with self.lock:
            return list(self.log)
