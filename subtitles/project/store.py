"""The project file (Repository): `dl-sub-dub.project.json`, next to the videos, or in the output folder when the
subtitle files go there (same rule as the old batch file).

The file is rewritten whole at each change, through a temporary file renamed over the old one: a crash in the middle
of a write leaves the previous version, never half a file. An old batch file (`subtitles.batch.json`) is read as a
project: its videos become items whose download is skipped, their states become states of the Subtitles and
Dubbing steps.
"""
import json
import time
from pathlib import Path

from .. import i18n, source
from ..states import VideoState
from .model import SCHEMA, Item, Project, Stage, StageState, new_id

FILE_NAME = "dl-sub-dub.project.json"
OLD_FILE_NAME = "subtitles.batch.json"


class ProjectFileError(source.SourceError):
    pass


def path_for(folder: Path, output_dir: Path | None, in_output_dir: bool) -> Path:
    return (Path(output_dir) if in_output_dir and output_dir else Path(folder)) / FILE_NAME


def find(folder: Path, output_dir: Path | None = None) -> Path | None:
    """Project file of a folder: the new one first, then an old batch file; in the folder, then in the output folder
    (which may serve other folders: its file counts only if it is about this one)."""
    for d in (Path(folder), Path(output_dir) if output_dir else None):
        if d is None:
            continue
        for name in (FILE_NAME, OLD_FILE_NAME):
            f = d / name
            if f.is_file() and (d == Path(folder) or _about(f, folder)):
                return f
    return None


def _about(file: Path, folder: Path) -> bool:
    try:
        d = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    root = d.get("folder") or d.get("root") or ""
    return bool(root) and Path(root).resolve() == Path(folder).resolve()


def save(project: Project, file: Path) -> None:
    file = Path(file)
    file.parent.mkdir(parents=True, exist_ok=True)
    project.updated = time.strftime("%Y-%m-%d %H:%M:%S")
    tmp = file.with_name(file.name + ".tmp")
    tmp.write_text(json.dumps(project.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(file)


def load(file: Path) -> Project:
    file = Path(file)
    if not file.is_file():
        raise ProjectFileError(i18n.t("err.project_file_not_found", path=file))
    try:
        d = json.loads(file.read_text(encoding="utf-8"))
    except ValueError as e:
        raise ProjectFileError(i18n.t("err.project_file_unreadable", error=e)) from e
    if "schema" in d:
        if d["schema"] > SCHEMA:
            raise ProjectFileError(i18n.t("err.project_file_newer", path=file))
        return Project.from_dict(d)
    if isinstance(d.get("videos"), list):
        return from_batch(d)
    raise ProjectFileError(i18n.t("err.project_file_no_videos"))


# --- Old batch files --------------------------------------------------------------------------------------------

DUBBING_STOPS = ("dubbing", "mux")


def from_batch(d: dict) -> Project:
    """An old batch (`subtitles.batch.json`) as a project. A video interrupted, or paused before its translation, starts
    again from the transcription, which is kept on the disk (a few seconds)."""
    options = dict(d.get("options") or {})
    stop = options.get("stop") or "translation"
    dub = stop in DUBBING_STOPS
    settings = {**options, "stop": stop}
    stages = [Stage.SUBTITLES] + ([Stage.DUBBING] if dub else [])
    items = [_item_of_video(v, dub) for v in d["videos"]]
    return Project(d.get("root") or "", items, settings, stages, id=d.get("id") or new_id(),
                   created=d.get("created") or time.time(), updated=d.get("updated") or "",
                   status=d.get("state") or "")


FACTS = ("duration", "count", "detected_language", "transcribed", "reviewed", "validated", "translated", "dubbed", "muxed")


def _item_of_video(v: dict, dub: bool) -> Item:
    item = Item(v["path"], title=v.get("name") or Path(v["path"]).name,
                overrides={"language": v["language"]} if v.get("language") else {},
                facts={k: v[k] for k in FACTS if v.get(k) is not None})
    state, error = v.get("state"), v.get("error") or ""
    sub = dub_state = StageState.TODO
    if state in (VideoState.TRANSLATED, VideoState.DONE):
        sub = StageState.DONE
        dub_state = StageState.DONE if state == VideoState.DONE and (v.get("muxed") or v.get("dubbed")) else StageState.TODO
    elif state == VideoState.ERROR:
        if dub and v.get("translated"):
            sub, dub_state = StageState.DONE, StageState.ERROR
        else:
            sub = StageState.ERROR
    elif state == VideoState.CANCELLED:
        sub = StageState.CANCELLED
    item.stages[Stage.SUBTITLES].state = sub
    item.stages[Stage.DUBBING].state = dub_state
    failed = Stage.DUBBING if dub_state == StageState.ERROR else Stage.SUBTITLES
    item.stages[failed].error = error if StageState.ERROR in (sub, dub_state) else ""
    return item
