"""Free space on the disk, checked before writing a large file (a download, an MKV): a clear message rather than a
file cut in the middle when the disk is full."""
import shutil
from pathlib import Path

from . import i18n, source

MARGIN = 512 * 2**20                             # kept free beyond what the file needs (the system, the other files)
MB_PER_MINUTE = {720: 12, 1080: 25, 1440: 50, 2160: 110, 0: 110}   # a generous size of a download per minute, by height


class NotEnoughSpace(source.SourceError):
    pass


def _existing(folder: Path) -> Path:
    """The folder itself, or its nearest parent that exists (the folder of a project may not be made yet)."""
    p = Path(folder)
    while not p.exists() and p.parent != p:
        p = p.parent
    return p


def check(folder: Path, need: int, usage=None):
    """Raises NotEnoughSpace when the disk of `folder` has less than `need` bytes free, plus the margin."""
    try:
        free = (usage or shutil.disk_usage)(_existing(folder)).free
    except OSError:
        return                                    # unknown (a network drive): the work goes on, as before
    if free < need + MARGIN:
        raise NotEnoughSpace(i18n.t("err.disk_full", folder=str(folder), need=_gb(need + MARGIN), free=_gb(free)))


def download_need(duration_s: float | None, max_height: int | None) -> int:
    """What a download may take, from its duration and the height asked (generous: better refuse than cut)."""
    if not duration_s:
        return 0
    heights = sorted(h for h in MB_PER_MINUTE if h)
    h = int(max_height or 0)
    rate = MB_PER_MINUTE[0] if h <= 0 else MB_PER_MINUTE[next((x for x in heights if x >= h), 0)]
    return int(duration_s / 60 * rate * 2**20)


def files_size(*paths) -> int:
    return sum(Path(p).stat().st_size for p in paths if p and Path(p).is_file())


def _gb(n: int) -> str:
    return f"{n / 2**30:.1f}"
