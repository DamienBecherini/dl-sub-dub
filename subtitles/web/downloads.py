"""The download of one item of a project (Command): started in its own thread, it can be cancelled at any time (yt-dlp
killed, partial files deleted, see source.download_youtube) or stopped by the pause of its project (partial files
kept: it resumes from them), and tells Fetching (fetching.py) how it ended.

Fetching starts at most `MAX_AT_ONCE` of them: downloads share the network, not the transcription server
nor the graphics card, so they run while the videos already there are being transcribed.
"""
import subprocess
import threading
from pathlib import Path

from .. import source, space
from ..cancellation import Cancellation
from .dlprogress import DownloadProgress

MAX_AT_ONCE = 2


class DownloadJob:
    def __init__(self, item_id: str, url: str, command: list[str], folder: Path, name: str, on_end, extra: list[str] = ()):
        self.item_id = item_id
        self.url = url
        self.command = command
        self.folder = Path(folder)
        self.name = name                       # yt-dlp template of the file name, without the extension
        self.on_end = on_end                   # on_end(job), from the thread of the job
        self.extra = list(extra)               # options of the tracks chosen (tracks.download_options)
        self.video: Path | None = None
        self.error = ""
        self.stopped = False                   # Stop of the project: the item goes back to do, instead of cancelled
        self.partial: Path | None = None       # stopped that way: the video whose partial files were kept
        self.progress = DownloadProgress(self.folder)      # where it stands, and the log of yt-dlp as a terminal shows it
        self.need = 0                          # bytes it may take (space.download_need), checked before it starts
        self.on_progress = None                # on_progress(): a line of yt-dlp came (the page has something new to show)
        self._cancelled = Cancellation()       # yt-dlp is killed the moment it is asked

    @property
    def log(self) -> list[str]:
        return self.progress.lines()

    @property
    def cancelled(self) -> bool:
        return self._cancelled()

    def start(self):
        threading.Thread(target=self.run, daemon=True, name=f"download-{self.item_id}").start()

    def cancel(self, stopped: bool = False):
        self.stopped = self.stopped or stopped
        self._cancelled.cancel()

    def write(self, line: str):
        """A message of the tool itself (not of yt-dlp)."""
        self.progress.note(str(line))

    def _line(self, line: str):
        self.progress.feed(line)
        if self.on_progress:
            self.on_progress()

    def run(self):
        try:
            space.check(self.folder, self.need)
            self.video = source.download_youtube(self.url, self.command, self.write, self.folder, self.name, self._cancelled,
                                                 self.extra, on_line=self._line, keep_partial=lambda: self.stopped)
        except source.DownloadCancelled as e:
            self._cancelled.cancel()
            self.partial = e.partial
        except (source.SourceError, OSError, subprocess.SubprocessError, ValueError) as e:
            self.error = str(e)
            self.write(self.error)
        finally:
            self.on_end(self)
