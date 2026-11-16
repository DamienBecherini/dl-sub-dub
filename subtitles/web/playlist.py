"""Download of a playlist, or of a list of addresses, in the background, followed by the page: the videos go one by
one into a folder of the output folder named after the playlist (or the list); the page then opens this folder like any source folder (selection, language
per video, project). Cancelling stops the video being downloaded at once (yt-dlp killed, its partial files deleted);
running it again resumes (videos already there are kept)."""
import subprocess
import threading
import uuid
from pathlib import Path

from .. import i18n, source
from ..cancellation import Cancellation
from ..states import DownloadState


class PlaylistDownload:
    def __init__(self, url: str, command: list[str], output_dir: Path, urls: list[str] | None = None, title: str = ""):
        self.id = uuid.uuid4().hex[:8]
        self.url = url
        self.urls = urls                       # list of addresses (then `title` names its folder) instead of one playlist
        self.command = command
        self.output_dir = Path(output_dir)
        self.state = DownloadState.LISTING
        self.title = title
        self.folder: Path | None = None
        self.total = 0
        self.done = 0
        self.downloaded = 0                    # videos in the folder at the end (already there or new)
        self.current = ""                      # title of the video being downloaded
        self.failures: list[dict] = []
        self.error = ""
        self.log: list[str] = []
        self._cancelled = Cancellation()

    def start(self):
        threading.Thread(target=self.run, daemon=True, name="playlist").start()

    def cancel(self):
        self._cancelled.cancel()

    def working(self) -> bool:
        return self.state in (DownloadState.LISTING, DownloadState.DOWNLOADING)

    def _progress(self, done: int, total: int, title: str):
        self.done, self.total, self.current = done, total, title

    def run(self):
        try:
            info = (source.list_info(self.urls, self.command, self.title) if self.urls
                    else source.playlist_info(self.url, self.command))
            if info is None:
                raise source.SourceError(i18n.t("err.not_a_playlist"))
            self.title, self.total = info["title"], len(info["entries"])
            self.folder = source.playlist_folder(self.output_dir, info)
            if self._cancelled():
                self.state = DownloadState.CANCELLED
                return
            self.state = DownloadState.DOWNLOADING
            files, self.failures = source.download_playlist(info, self.command, self.folder, self.log.append,
                                                             self._cancelled, self._progress)
            self.downloaded = len(files)
            self.state = DownloadState.CANCELLED if self._cancelled() else DownloadState.DONE
        except (source.SourceError, OSError, subprocess.SubprocessError, ValueError) as e:
            self.state, self.error = DownloadState.ERROR, str(e)

    def to_dict(self) -> dict:
        return {"id": self.id, "url": self.url, "state": self.state, "title": self.title, "total": self.total, "done": self.done,
                "current": self.current, "downloaded": self.downloaded, "folder": str(self.folder).replace("\\", "/") if self.folder else "",
                "failures": self.failures, "error": self.error}
