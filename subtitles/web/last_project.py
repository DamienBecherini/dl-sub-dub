"""The last project left unfinished, offered for resuming at the start of the next session: its path, kept in a small
file of the user folder each time it is saved, forgotten once it is finished or on "Ignore"."""
import json
from pathlib import Path

from .. import config, source
from ..project import store
from ..states import FINISHED_RUN_STATES, VideoState
from .runs import ProjectRun

FILE = config.user_dir() / "last_project.json"   # path of the file of the last unfinished project
OLD_NAME = "last_batch.json"                     # its name before: read once, then replaced


def remember(run: ProjectRun):
    """After each save of a project (ProjectRun.after_save): an unfinished one becomes the last; a finished one that was
    the last is forgotten."""
    try:
        if run.state not in FINISHED_RUN_STATES:
            FILE.parent.mkdir(parents=True, exist_ok=True)
            FILE.write_text(json.dumps({"file": str(run.file)}, ensure_ascii=False), encoding="utf-8")
            FILE.with_name(OLD_NAME).unlink(missing_ok=True)
        elif written() == str(run.file):
            forget()
    except OSError:
        pass


def written() -> str:
    """The path written in the file ("" if none)."""
    for file in (FILE, FILE.with_name(OLD_NAME)):
        try:
            return json.loads(file.read_text(encoding="utf-8")).get("file", "")
        except (OSError, ValueError):
            continue
    return ""


def offered(open_ids) -> dict | None:
    """The project left unfinished at the end of the previous session, or None (finished, among `open_ids`: already
    open, or its file gone)."""
    file = written()
    if not file:
        return None
    try:
        run = ProjectRun(store.load(Path(file)), Path(file))
    except source.SourceError:
        return None
    p = run.project
    states = [v["state"] for v in run.videos]
    if p.status in FINISHED_RUN_STATES or p.id in open_ids or all(s == VideoState.DONE for s in states):
        return None
    return {"file": str(run.file).replace("\\", "/"), "root": p.folder, "state": p.status, "updated": p.updated,
            "total": len(states), "done": sum(1 for s in states if s == VideoState.DONE), "translator": p.settings.get("translator", ""),
            "stages": [str(s) for s in p.stages]}


def forget():
    """"Ignore": the project is no longer offered; its file stays, for "Resume a project…"."""
    FILE.unlink(missing_ok=True)
    FILE.with_name(OLD_NAME).unlink(missing_ok=True)
