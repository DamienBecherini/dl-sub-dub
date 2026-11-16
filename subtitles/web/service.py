"""What the whole service shares: its work (the task queue and the projects, `manager`), the download of a playlist
and the inventory of a folder shown on the page (one at a time each), and whether it is busy, for the "local service"
contract (GET /api/health) and the stop after an hour without activity. The routes and the tests reach them here:
`service.manager`."""
from .. import __version__, config, folder, i18n
from .idle import IdleWatch
from .manager import Manager
from .playlist import PlaylistDownload

manager = Manager()
imposed_timeout: float | None = None   # --idle of the command line
current_scan: folder.FolderScan | None = None   # inventory of the folder in progress or displayed (one at a time)
current_download: PlaylistDownload | None = None   # download of a playlist in progress or displayed (one at a time)


def busy() -> bool:
    """Work in progress: tasks and projects, or the download of a playlist (the service must not stop under it)."""
    return manager.busy() or bool(current_download and current_download.working())


def activity() -> str:
    if current_download and current_download.working() and not manager.busy():
        return i18n.t("activity.playlist", done=current_download.done, n=current_download.total)
    return manager.activity()


def idle_timeout_s() -> float:
    if imposed_timeout is not None:
        return imposed_timeout
    try:
        return float(config.load().get("web", {}).get("idle_min", 60)) * 60
    except (config.ConfigError, OSError, ValueError):
        return 0                        # unreadable configuration: do not stop


idle = IdleWatch(idle_timeout_s, lambda: busy())


def health() -> dict:
    """Answer of GET /api/health, following the "local service" contract v2."""
    remaining = idle.remaining_s()
    return {"state": "ready", "contract": 2, "service": "subtitles", "version": __version__,
            "busy": busy(), "activity": activity(), "remaining_work_s": manager.remaining_s(),
            "idle": {"timeout_s": idle_timeout_s(), "remaining_s": None if remaining is None else round(remaining, 1)}}
