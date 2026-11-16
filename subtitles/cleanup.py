"""Cleanup of the working folders (`<output_dir>/.work/<name>/`) by category of files.

Categories (`[cleanup]` options of the configuration, `true` = delete):
  audio          `<name>.audio.*`                             the extracted track, the largest file (redone if needed, a few seconds);
                 `dubbing/temp-<language>/`                    and what a stopped dubbing left: the temporary folder of each language
                 `dubbing/separation/`                         (of no use to a resume) and, for "Clean now" only, what a resume
                 `dubbing/sentences-<language>/`               takes again (a stop keeps it): the voice separated from the
                                                              background, the sentences already made of each voice
  subtitles      `*.srt`, `*.vtt`                              the copies of the working folder (the useful files are next to the video)
  transcription  `transcription.json`, `translation*.json`     raw data: kept by default, they allow adding a language or
                                                              changing the layout without transcribing or translating again
  logs           `log-*.txt`                                   kept by default; those older than `logs_age_h` hours are purged
                                                              when the interface starts and by "Clean now"
  dubbing        `dubbing/*`                                   the dubbed voice of each language: kept by default (without it,
                                                              making the MKV again or adding a track means dubbing again)

Limits of the whole `.work` folder (`max_age_days`, `max_size_mb`, 0 = none): the working folders of the oldest videos go
entirely (their transcription.json too), when the interface starts and by "Clean now"; nothing next to the videos is
touched, and a folder changed in the last minutes (a task at work) stays.
"""
import time
from pathlib import Path

from . import i18n

CATEGORIES = {
    "audio": ["*.audio.*", "*.m4a", "*.opus", "*.mp3", "*.ogg", "*.flac", "*.wav"],   # bare extensions: name used by the first versions
    "subtitles": ["*.srt", "*.vtt"],
    "transcription": ["transcription.json", "translation*.json"],
    "logs": ["log-*.txt"],
    "dubbing": ["dubbing/*"],
}
DEFAULTS = {"audio": True, "subtitles": True, "transcription": False, "logs": False, "logs_age_h": 48, "dubbing": False,
            "max_age_days": 0, "max_size_mb": 0}
RECENT_S = 600                                  # a working folder changed this recently is in use: never removed whole


def options(cfg: dict) -> dict:
    return {**DEFAULTS, **(cfg.get("cleanup") or {})}


def _resumable_folders(work: Path) -> list[Path]:
    folder = work / "dubbing"
    return [folder / "separation", *folder.glob("sentences-*")]


def dubbing_leftovers(work: Path, resumable: bool = False) -> list[Path]:
    """The files a stopped dubbing left in `dubbing/`: the temporary folder of each language (`temp-<language>`: the
    extract, a separation cut short, the voice reference of the last sentence; of no use to a resume) and, if
    `resumable`, what a resume takes again: the voice separated from the background (about 1 GB for 50 minutes of
    video) and the sentences already made of each voice (`sentences-<language>`)."""
    folder = work / "dubbing"
    folders = [*folder.glob("temp-*"), *(_resumable_folders(work) if resumable else [])]
    return sorted(f for d in folders if d.is_dir() for f in d.rglob("*") if f.is_file())


def _drop_empty_dubbing_folders(work: Path):
    folder = work / "dubbing"
    for d in [*folder.glob("temp-*"), *_resumable_folders(work)]:
        if d.is_dir():
            for sub in sorted((p for p in d.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True) + [d]:
                try:
                    sub.rmdir()                # only if empty
                except OSError:
                    pass


def clean_stopped_dubbing(work: Path) -> tuple[int, int]:
    """A dubbing stopped or cancelled: the temporary folders of its languages go; the separated voice and the sentences
    already made stay for the resume."""
    r = _delete(dubbing_leftovers(work))
    _drop_empty_dubbing_folders(work)
    return r


def classify(work: Path, resumable: bool = False) -> dict[str, list[Path]]:
    """Files of a working folder by category. What a stopped dubbing left counts as audio (`resumable`: what a
    resume takes again too, see dubbing_leftovers)."""
    r = {}
    for cat, patterns in CATEGORIES.items():
        seen = []
        for pattern in patterns:
            seen.extend(p for p in work.glob(pattern) if p.is_file())
        r[cat] = sorted(set(seen))
    r["audio"] = sorted({*r["audio"], *dubbing_leftovers(work, resumable)})
    return r


def _delete(files: list[Path]) -> tuple[int, int]:
    count = size = 0
    for f in files:
        try:
            n = f.stat().st_size
            f.unlink()
            count += 1
            size += n
        except OSError:
            pass
    return count, size


def clean_folder(work: Path, opts: dict, log=lambda _m: None, keep: set[Path] | None = None,
                 old_logs_only: bool = True, resumable: bool = False) -> tuple[int, int]:
    """Applies the options to a working folder. `keep`: files not to touch (only copy of a result).
    Logs are deleted only if they are older than `logs_age_h` hours (unless asked otherwise). `resumable`: the voice
    separated from the background and the sentences already made go with the audio too ("Clean now"; at the end of a
    task they stay for a resume)."""
    if not work.is_dir():
        return 0, 0
    keep = {p.resolve() for p in (keep or set())}
    limit = _log_limit(float(opts.get("logs_age_h", 48)))
    total_count = total_bytes = 0
    for cat, files in classify(work, resumable).items():
        if not opts.get(cat):
            continue
        targets = [f for f in files if f.resolve() not in keep]
        if cat == "logs" and old_logs_only:
            targets = [f for f in targets if f.stat().st_mtime <= limit]
        count, size = _delete(targets)
        if count:
            log(i18n.t("log.cleanup", category=i18n.label("cleanup.", cat), n=count, mb=f"{size / 1e6:.1f}"))
        total_count += count
        total_bytes += size
    _drop_empty_dubbing_folders(work)
    return total_count, total_bytes


def _log_limit(age_h: float) -> float:
    """Date before which a log is old enough to go. 0 hours: every log, even one just written (on Windows the date of
    a file can be a few milliseconds ahead of time.time())."""
    return float("inf") if age_h <= 0 else time.time() - age_h * 3600


def purge_logs(root: Path, age_h: float) -> tuple[int, int]:
    """Logs older than `age_h` hours in every working folder."""
    if not root.is_dir():
        return 0, 0
    limit = _log_limit(age_h)
    return _delete([f for f in root.glob("*/log-*.txt") if f.is_file() and f.stat().st_mtime <= limit])


def summary(root: Path) -> dict[str, dict]:
    """Number of files and bytes per category over every working folder."""
    r = {cat: {"files": 0, "bytes": 0} for cat in CATEGORIES}
    if root.is_dir():
        for d in root.iterdir():
            if d.is_dir():
                for cat, files in classify(d, resumable=True).items():
                    r[cat]["files"] += len(files)
                    r[cat]["bytes"] += sum(f.stat().st_size for f in files)
    return r


def clean_all(root: Path, opts: dict) -> dict[str, dict]:
    """"Clean now": applies the options to every working folder (logs: only the old ones), then deletes the empty
    folders. Returns {category: {files, bytes}} deleted."""
    r = {cat: {"files": 0, "bytes": 0} for cat in CATEGORIES}
    if not root.is_dir():
        return r
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        before = classify(d, resumable=True)
        sizes = {f: f.stat().st_size for files in before.values() for f in files}
        clean_folder(d, opts, resumable=True)             # nothing runs during "Clean now": no resume to keep it for
        after = classify(d, resumable=True)
        for cat in CATEGORIES:
            remaining = set(after[cat])
            gone = [f for f in before[cat] if f not in remaining]
            r[cat]["files"] += len(gone)
            r[cat]["bytes"] += sum(sizes[f] for f in gone)
        for empty in (d / "dubbing", d):
            try:
                if empty.is_dir() and not any(empty.iterdir()):
                    empty.rmdir()
            except OSError:
                pass
    r["folders"] = limit_work(root, opts)
    return r


def _folder_state(d: Path) -> tuple[float, int]:
    """(date of the most recent file, total size) of a working folder."""
    newest, size = d.stat().st_mtime, 0
    for f in d.rglob("*"):
        if f.is_file():
            st = f.stat()
            newest, size = max(newest, st.st_mtime), size + st.st_size
    return newest, size


def limit_work(root: Path, opts: dict, now: float | None = None) -> dict:
    """The limits of the whole `.work` folder: the working folders not changed for `max_age_days` days go, then the
    oldest ones while the total is over `max_size_mb`. Returns {files: folders removed, bytes}."""
    import shutil
    age_days, size_mb = float(opts.get("max_age_days") or 0), float(opts.get("max_size_mb") or 0)
    gone = {"files": 0, "bytes": 0}
    if not root.is_dir() or (age_days <= 0 and size_mb <= 0):
        return gone
    now = time.time() if now is None else now
    folders = sorted(((*_folder_state(d), d) for d in root.iterdir() if d.is_dir()), key=lambda x: x[0])
    total = sum(size for _, size, _ in folders)

    def remove(size, d):
        nonlocal total
        shutil.rmtree(d, ignore_errors=True)
        if not d.exists():
            gone["files"] += 1
            gone["bytes"] += size
            total -= size

    for newest, size, d in folders:
        if now - newest < RECENT_S:
            continue
        if age_days > 0 and now - newest > age_days * 86400:
            remove(size, d)
        elif size_mb > 0 and total > size_mb * 1e6:
            remove(size, d)
    return gone
