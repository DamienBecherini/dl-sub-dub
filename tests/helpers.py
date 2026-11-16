"""Projects at work written as a short list of videos ({path, state, task…}), each in one state of the old kind,
turned into items whose steps are in the matching states. Keeps the tests of the scheduling short and readable."""
import re
from pathlib import Path

from subtitles import steps
from subtitles.project import Item, Project, Stage, StageState
from subtitles.project.choices import stages_for
from subtitles.web.runs import ProjectRun

S = StageState
_UNSET = object()
FACTS = ("count", "detected_language", "duration", "transcribed", "reviewed", "validated", "translated", "dubbed", "muxed")


def Run(root: str, videos: list[dict], options: dict, file, ident: str | None = None) -> ProjectRun:  # noqa: N802 - reads like a class
    items = [Item(v["path"], title=v.get("name") or "") for v in videos]
    project = Project(root, items, dict(options), stages_for(options.get("stop")))
    if ident:
        project.id = ident
    run = ProjectRun(project, Path(file))
    for i, v in enumerate(videos):
        update(run, i, **{k: x for k, x in v.items() if k not in ("path", "name")})
    return run


def update(run: ProjectRun, index: int, **fields) -> Item:
    """Changes a video as the old tests did (`project.videos[i].update(...)`): state and task go to the steps (state
    "dubbing": subtitles done, dubbing running), language to the choices of the item, the rest to what is known of it."""
    item = run.items()[index]
    state, task = fields.pop("state", None), fields.pop("task", _UNSET)
    started = fields.pop("started", None)
    fields.pop("error", None)
    if "language" in fields:
        language = fields.pop("language")
        if language:
            item.overrides["language"] = language
        else:
            item.overrides.pop("language", None)
    item.facts.update({k: v for k, v in fields.items() if k in FACTS})
    sub, dub = item.stages[Stage.SUBTITLES], item.stages[Stage.DUBBING]
    if state is not None:
        sub.state, dub.state = {
            "todo": (S.TODO, S.TODO), "running": (S.RUNNING, S.TODO), "dubbing": (S.DONE, S.RUNNING), "prepared": (S.PAUSED, S.TODO),
            "translated": (S.DONE, S.TODO), "done": (S.DONE, S.DONE if steps.goes_to(run.options.get("stop"), "dubbing") else S.TODO),
            "error": (S.ERROR, S.TODO), "cancelled": (S.CANCELLED, S.TODO)}[state]
        if state == "error":
            sub.error = "error"
    if task is not _UNSET:
        (dub if dub.state == S.RUNNING else sub).task = task
    if started is not None:
        sub.started = dub.started = started
    return item


def at(item: Item, stage: Stage, state: StageState, error: str = "", task: str | None = None) -> Item:
    """Places a step of a video in a state, as a project read from its file would have it: the tests start from there,
    the code under test then reports events (Item.apply)."""
    status = item.stages[stage]
    status.state, status.error = state, error
    if task is not None:
        status.task = task
    return item


def titled(template: str, title: str) -> str:
    """A yt-dlp template of a file name with its title filled in, as yt-dlp does ("%(title).180B": the title, cut)."""
    return re.sub(r"%\(title\)\.\d+B", title, template)
