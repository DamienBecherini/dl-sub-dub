"""Where the user's data is: the settings, the history, the measured speeds and the last project, in a folder chosen
in Settings (one that is backed up, for instance), by default the fixed folder of the application.

The fixed folder (config.state_dir, %LOCALAPPDATA%\\subtitles on Windows) never moves: it keeps what belongs to this
machine (log, lock, window, thumbnails) and location.json, which names the data folder ({"data": path}). Without
that file, the data is in the fixed folder, as before.

A move copies the files, checks each copy, writes location.json and only then deletes the originals; the running
service then uses the new folder at once (repoint), without a restart."""
import json
import os
import shutil
from pathlib import Path

from . import config, i18n

DATA_FILES = ("config.json", "history.json", "speeds.json", "last_project.json")
DATA_FOLDERS = ("function_words",)        # the user's lists of function words (language.py): they move with the settings


def current() -> Path:
    return config.user_dir()


def _target(path: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(str(path).strip())))


def _same(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return False


def analyze(path: str) -> dict:
    """What the chosen folder holds, to offer the right choice: {path, exists, same, has_data, error?}."""
    p = _target(path)
    info = {"path": str(p), "exists": p.is_dir(), "same": _same(p, current()), "has_data": False}
    if not str(path).strip() or not p.is_absolute():
        info["error"] = i18n.t("err.location_absolute")
    elif p.exists() and not p.is_dir():
        info["error"] = i18n.t("err.location_file")
    elif p.is_dir():
        info["has_data"] = any((p / name).is_file() for name in DATA_FILES)
    return info


def move(path: str, action: str = "move") -> str:
    """action "move": our files go to the folder (refused if it already holds data, so that nothing is overwritten);
    "use": the data already in the folder is used, ours stays where it is. Returns the message for the user."""
    info = analyze(path)
    if info.get("error"):
        raise ValueError(info["error"])
    target, source = Path(info["path"]), current()
    if info["same"]:
        return i18n.t("cfg.location_same", path=target)
    if action == "use":
        _write(target)
        return i18n.t("cfg.location_used", path=target)
    if info["has_data"]:
        raise ValueError(i18n.t("err.location_has_data", path=target))
    target.mkdir(parents=True, exist_ok=True)
    copies = []
    for name in DATA_FILES:
        src, dest = source / name, target / name
        if not src.is_file():
            continue
        shutil.copy2(src, dest)
        if dest.stat().st_size != src.stat().st_size:
            raise OSError(i18n.t("err.location_copy", name=name))
        copies.append(src)
    folders = []
    for name in DATA_FOLDERS:
        src, dest = source / name, target / name
        if not src.is_dir():
            continue
        shutil.copytree(src, dest, dirs_exist_ok=True)
        for f in src.rglob("*"):
            if f.is_file() and (not (dest / f.relative_to(src)).is_file() or (dest / f.relative_to(src)).stat().st_size != f.stat().st_size):
                raise OSError(i18n.t("err.location_copy", name=f"{name}/{f.relative_to(src).as_posix()}"))
        folders.append(src)
    _write(target)
    for src in copies:                    # the copies are checked and the location written: the originals can go
        src.unlink(missing_ok=True)
    for src in folders:
        shutil.rmtree(src, ignore_errors=True)
    return i18n.t("cfg.location_moved", n=len(copies), path=target)


def _write(target: Path):
    """location.json in the fixed folder; back in the fixed folder itself: the file is deleted (the default)."""
    fixed = config.state_dir()
    file = fixed / config.LOCATION_FILE
    if _same(target, fixed):
        file.unlink(missing_ok=True)
    else:
        fixed.mkdir(parents=True, exist_ok=True)
        partial = file.with_name(file.name + ".partial")
        partial.write_text(json.dumps({"data": str(target)}, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(partial, file)
    repoint()


def repoint():
    """The modules that keep the path of a data file take the new one (the service goes on without a restart)."""
    from . import language
    from .web import last_project, tasks, timing
    folder = current()
    config.USER_FILE = folder / "config.json"
    language.USER_LISTS = folder / "function_words"
    tasks.HISTORY_FILE = folder / "history.json"
    timing.FILE = folder / "speeds.json"
    last_project.FILE = folder / "last_project.json"
    with config._CACHE_LOCK:
        config._CACHE.clear()
