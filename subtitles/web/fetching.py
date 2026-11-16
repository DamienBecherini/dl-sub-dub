"""What the projects fetch from the sites: the downloads of their addresses (two at a time, see downloads.py), what
each site offers for a video (read in the background), and the tracks added to a video already downloaded.

All of it runs beside the task queue, in threads of its own: the queue runs one task at a time (a transcription lasts
minutes), so a video ticked for download meanwhile would otherwise wait for its end. The orchestrator owns one
`Fetching`, shares its projects with it and is told when a download ends (`ended`), to start the next step of a video.
"""
import logging
import subprocess
import threading
from pathlib import Path
from typing import Callable

from .. import config, source, space, tracks
from ..project import Event, Item, Stage, StageState
from ..project.choices import origin_of
from ..states import RunState
from . import background, downloads, observed
from .runs import ProjectRun

LOG = logging.getLogger(__name__)
S = StageState
E = Event


class Fetching:
    INFO_AT_ONCE = 2                    # sites read at once (yt-dlp -J, several seconds each)

    def __init__(self, runs: dict[str, ProjectRun], wake: Callable[[], None], ended: Callable[[ProjectRun, str], None]):
        self.runs = runs                                         # the projects of the orchestrator (the same dict)
        self.wake = wake                                         # wakes the loop of the queue
        self.ended = ended                                       # a download ended: the orchestrator starts what follows
        self.downloads: dict[str, downloads.DownloadJob] = {}    # item id → its download in progress
        self.logs: dict[str, list[str]] = {}                     # item id → log of its last download (ended)
        self.reading: set[str] = set()                           # items whose site information is being read
        self.adding: set[str] = set()                            # items getting tracks added after their download
        self.poke = threading.Event()                            # something to download may have changed

    def start(self):
        """The thread of the downloads and of the reading of the sites: every second, or at once when poked."""
        threading.Thread(target=self._loop, daemon=True, name="downloads").start()

    def _loop(self):
        while True:
            self.poke.wait(timeout=1)
            self.poke.clear()
            try:
                self.feed_all()
            except Exception:                  # noqa: BLE001 - the thread must live on
                LOG.exception("feeding the downloads")

    def feed_all(self):
        for run in list(self.runs.values()):
            if run.state == RunState.CANCELLED or not run.lock.acquire(timeout=1):
                continue                       # busy (freeing the graphics card): the next turn
            try:
                self.feed_infos(run)
                if run.state == RunState.RUNNING:
                    self.feed_downloads(run)
            finally:
                run.lock.release()

    # --- downloads ---
    def feed_downloads(self, run: ProjectRun):
        """Starts the downloads of the addresses ticked and left to do, up to the number allowed at once."""
        if not run.wants(Stage.DOWNLOAD):
            return
        cfg = None
        for item in run.items():
            if len(self.downloads) >= downloads.MAX_AT_ONCE:
                return
            if not item.is_address or not item.ticked(Stage.DOWNLOAD) or item.state(Stage.DOWNLOAD) != S.TODO or item.id in self.downloads:
                continue
            cfg = cfg or config.load()
            self.start_download(run, item, cfg)

    def start_download(self, run: ProjectRun, item: Item, cfg: dict):
        run.project.ran = True
        s = run.settings(item, cfg)
        ytdlp = {**(cfg.get("youtube") or {}), "max_height": s["max_height"]}
        n = run.project.items.index(item) + 1
        command = source.ytdlp_command({**cfg, "youtube": ytdlp})
        if not item.apply(Stage.DOWNLOAD, E.START):
            return
        try:
            name = source.name_template(Path(run.root), f"{n:0{max(3, len(str(len(run.project.items))))}d} - ")
            extra = tracks.download_options(command, Path(run.root), name, int(s["max_height"] or 0), s["audio"], s["site_subtitles"])
        except source.SourceError as e:                    # a folder too deep for any file name, a track that is no language
            item.apply(Stage.DOWNLOAD, E.FAIL, error=str(e))
            run.save()
            return
        item.facts["tracks"] = {"audio": list(s["audio"] or []), "subtitles": list(s["site_subtitles"] or [])}
        job = downloads.DownloadJob(item.id, item.source, command, Path(run.root), name,
                                    lambda j, run=run: self.download_ended(run, j), extra)
        job.need = space.download_need(item.facts.get("duration") or (item.facts.get("site") or {}).get("duration"), s["max_height"])
        job.on_progress = run.changes.tell
        self.downloads[item.id] = job
        job.start()
        run.save()

    def download_ended(self, run: ProjectRun, job: downloads.DownloadJob):
        with run.lock:
            self.downloads.pop(job.item_id, None)
            self.logs[job.item_id] = job.log
            item = run.project.item(job.item_id)
            if item is None:
                return
            if job.video is not None:
                item.video = str(job.video).replace("\\", "/")
                item.title = item.title or job.video.stem
                item.apply(Stage.DOWNLOAD, E.FINISH)
                if item.facts.pop("partial", None):         # resumed after a pause: what another format may have left
                    source.remove_partial_files(job.video, thumbnails=False)
                observed.mark_transcribed(run)
                observed.measure_durations(run, [item])
            elif job.cancelled:
                if job.partial is not None and run.state != RunState.CANCELLED:
                    item.facts["partial"] = job.partial.as_posix()      # a pause: yt-dlp resumes from these files
                else:
                    if job.partial is not None:                         # paused, then its project cancelled
                        source.remove_partial_files(job.partial)
                    self.drop_partial(run, item)                        # cancelled after a pause: nothing is left
                item.apply(Stage.DOWNLOAD, E.PUT_BACK if job.stopped else E.CANCEL)    # stopped, then its project cancelled: stays cancelled
            else:
                item.apply(Stage.DOWNLOAD, E.FAIL, error=job.error)
            run.save()
        self.ended(run, job.item_id)
        self.wake()

    def stop(self, item_id: str, stopped: bool = False) -> bool:
        """Stops the download of a video, if any: `stopped` keeps its partial files (Stop), otherwise they go (cancel)."""
        job = self.downloads.get(item_id)
        if job:
            job.cancel(stopped=stopped)
        return job is not None

    @staticmethod
    def drop_partial(run: ProjectRun, item: Item):
        """The partial files a paused download kept, deleted: its line is removed, or its project cancelled. Only in
        the folder of the project, where the downloads go: the path comes from the project file, which may have been
        written by someone else."""
        kept = item.facts.pop("partial", None)
        if kept and Path(kept).parent == Path(run.root):
            source.remove_partial_files(Path(kept))

    def log(self, item_id: str) -> list[str]:
        """The log of the download under way, otherwise of the last one (until the service stops)."""
        job = self.downloads.get(item_id)
        return list(job.log) if job else list(self.logs.get(item_id, []))

    def progress(self, item_id: str) -> dict | None:
        job = self.downloads.get(item_id)
        return job.progress.to_dict() if job else None

    # --- what the site offers for a video, read in the background (several seconds per video) ---
    def feed_infos(self, run: ProjectRun):
        for item in list(run.project.items):
            if len(self.reading) >= self.INFO_AT_ONCE:
                return
            wanted = item.is_address or (item.facts.get("site_wanted") and origin_of(item))     # a file: only when asked
            if wanted and "site" not in item.facts and "site_error" not in item.facts and item.id not in self.reading:
                self.reading.add(item.id)
                background.spawn(f"info-{item.id}", self.read_info, run, item)

    def read_info(self, run: ProjectRun, item: Item):
        try:
            info = tracks.site_info(origin_of(item), source.ytdlp_command(config.load()))
            with run.lock:
                item.facts["site"] = info
                item.title = item.title or info["title"]
                item.facts["duration"] = item.facts.get("duration") or info["duration"]
        except (source.SourceError, OSError, subprocess.SubprocessError, ValueError) as e:
            item.facts["site_error"] = str(e)
        finally:
            self.reading.discard(item.id)
            run.save()
            self.wake()

    def _item(self, ident: str, item_id: str) -> tuple[ProjectRun | None, Item | None]:
        run = self.runs.get(ident)
        return run, (run.project.item(item_id) if run else None)

    def read_info_again(self, ident: str, item_id: str) -> bool:
        run, item = self._item(ident, item_id)
        if item is None or not origin_of(item):
            return False
        item.facts.pop("site", None)
        item.facts.pop("site_error", None)
        if not item.is_address:
            item.facts["site_wanted"] = True       # a file: its site is read because the user asked
        run.changes.tell()
        self.wake()
        return True

    def set_origin(self, ident: str, item_id: str, address: str) -> bool:
        """The address a video file comes from, given by the user (to take tracks of its site); "": forgotten, the one
        written in the file is read again. Refused for an address of the list, and for anything but http(s)."""
        run, item = self._item(ident, item_id)
        address = (address or "").strip()
        if item is None or item.is_address or item.id in self.adding \
                or (address and (not address.startswith(("http://", "https://")) or " " in address)):
            return False
        with run.lock:
            same = bool(address) and address == item.facts.get("address")     # the same one: only its site is forgotten
            for key in ("site", "site_error", "site_wanted", "tracks_error", *(() if same else ("address", "address_given", "tracks"))):
                item.facts.pop(key, None)
            if address and not same:
                item.facts["address"], item.facts["address_given"] = address, True
            run.save()
        if not address:
            observed.measure_durations(run, [item])
        return True

    # --- tracks added after the download, without downloading the video again ---
    def add_tracks(self, ident: str, item_id: str, audio: list[str], subtitles: list[dict]) -> bool:
        run, item = self._item(ident, item_id)
        if item is None or not origin_of(item) or not item.video or (item.is_address and item.state(Stage.DOWNLOAD) != S.DONE) \
                or item.id in self.adding or not (audio or subtitles):
            return False
        self.adding.add(item.id)
        item.facts["adding"] = True
        item.facts.pop("tracks_error", None)
        run.save()
        background.spawn(f"tracks-{item.id}", self._add_tracks, run, item, list(audio), list(subtitles))
        return True

    def _add_tracks(self, run: ProjectRun, item: Item, audio: list[str], subtitles: list[dict]):
        command = source.ytdlp_command(config.load())
        video = Path(item.video)
        done = item.facts.setdefault("tracks", {"audio": [], "subtitles": []})
        try:
            if subtitles:
                tracks.fetch_subtitles(origin_of(item), command, video.parent, video.stem, subtitles)
                done["subtitles"] = [*done.get("subtitles", []), *[s for s in subtitles if s not in done.get("subtitles", [])]]
            for lang in audio:
                if not done.get("audio"):              # the track of the first download: the original
                    original = next((a["language"] for a in item.facts.get("site", {}).get("audio", []) if a["kind"] == "original"), None)
                    done["audio"] = [original] if original else []
                tracks.add_audio(origin_of(item), command, video, lang)
                done["audio"] = [*done["audio"], lang]
        except (source.SourceError, OSError, subprocess.SubprocessError, ValueError) as e:
            item.facts["tracks_error"] = str(e)
        finally:
            item.facts.pop("adding", None)
            self.adding.discard(item.id)
            run.save()
            self.wake()
