"""Data of a project: plain dataclasses, written to and read from JSON without loss (`to_dict` / `from_dict`).

No behaviour here beyond reading and changing these data: running a step belongs to the orchestrator, checking the
disk to `stages`. Every state is a StrEnum, so the JSON file, the API and the page share the same words.

A step of a video is a small state machine: its state changes only through `Item.apply(stage, event)`, and
`TRANSITIONS` says what each event gives in each state. Whoever reports something (the queue, a download, the disk, a
button) says what happened, not which state to write; an event that means nothing in the current state (the end of a
task arriving after its step was cancelled) changes nothing.
"""
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import StrEnum

SCHEMA = 2                                         # version of the project file (2: the steps of the project are the ticked ones)
LOG = logging.getLogger(__name__)


def new_id() -> str:
    return uuid.uuid4().hex[:8]


class Stage(StrEnum):
    DOWNLOAD = "download"
    SUBTITLES = "subtitles"                        # transcription, review of the names, translation
    DUBBING = "dubbing"                            # dubbed voice, then MKV with every track


STAGES = (Stage.DOWNLOAD, Stage.SUBTITLES, Stage.DUBBING)   # in the order they run


class StageState(StrEnum):
    TODO = "todo"
    RUNNING = "running"
    PAUSED = "paused"                              # subtitles: transcribed, waiting before the translation (cost, names)
    DONE = "done"
    ERROR = "error"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"                            # does not apply: a video file needs no download


FINISHED = (StageState.DONE, StageState.SKIPPED)
LEFT = (StageState.TODO, StageState.ERROR, StageState.CANCELLED)       # work a Run takes: to do, or to do again
BUSY = (StageState.RUNNING, StageState.PAUSED)                         # a task or a download holds the step


class Event(StrEnum):
    """What happens to a step of a video."""
    START = "start"                                # its work begins: Run, Retry, Translate after its pause, a step done made again
    WAIT = "wait"                                  # subtitles: transcribed, waiting for the go-ahead of the translation
    FINISH = "finish"                              # its work ended well
    FAIL = "fail"                                  # its work failed, or could not start
    CANCEL = "cancel"                              # Cancel: stays cancelled until asked again
    PUT_BACK = "put_back"                          # Stop, or a task that ended before reaching it: back to do
    RESET = "reset"                                # to do again: Retry later, a setting changed (a language added)
    REOPEN = "reopen"                              # its project is read back from its file: nothing runs any more
    FOUND_DONE = "found_done"                      # the disk shows it done (work of a previous project, of the command line)
    DROP = "drop"                                  # does not apply: a video file needs no download, nothing to dub


_T, _R, _P, _D, _E, _C, _S = (StageState.TODO, StageState.RUNNING, StageState.PAUSED, StageState.DONE, StageState.ERROR,
                              StageState.CANCELLED, StageState.SKIPPED)
# event → {state it means something in: state it gives}. A state missing from the row of an event: the event is
# ignored there. A step that does not apply (skipped) never starts; one being worked on is never found done, dropped
# nor reset from outside: its task or its download says how it ends.
TRANSITIONS: dict[Event, dict[StageState, StageState]] = {
    Event.START: {_T: _R, _P: _R, _D: _R, _E: _R, _C: _R},
    Event.WAIT: {_R: _P},
    Event.FINISH: {_R: _D},
    Event.FAIL: {_R: _E, _T: _E, _P: _E, _E: _E},
    Event.CANCEL: {_R: _C, _P: _C, _C: _C},
    Event.PUT_BACK: {_R: _T},
    Event.RESET: {_E: _T, _C: _T, _D: _T, _S: _T, _T: _T},
    Event.REOPEN: {_R: _T, _P: _T, _E: _T, _C: _T, _T: _T, _D: _D, _S: _S},
    Event.FOUND_DONE: {_T: _D, _C: _D, _E: _D, _S: _D, _D: _D},
    Event.DROP: {_T: _S, _D: _S, _E: _S, _C: _S, _S: _S},
}


@dataclass
class StageStatus:
    """Where an item stands in one step, and whether it is ticked for that step (each screen of a step has its own
    ticks: four videos to download, two of them to subtitle)."""
    state: StageState = StageState.TODO
    task: str | None = None                        # task running or paused for this step
    error: str = ""
    started: float | None = None
    finished: float | None = None
    selected: bool = True

    def to_dict(self) -> dict:
        return {"state": self.state, "task": self.task, "error": self.error, "started": self.started, "finished": self.finished,
                "selected": self.selected}

    @classmethod
    def from_dict(cls, d: dict) -> "StageStatus":
        return cls(StageState(d.get("state") or StageState.TODO), d.get("task"), d.get("error") or "", d.get("started"), d.get("finished"),
                   d.get("selected", True) is not False)


@dataclass
class Item:
    """One video of the project: an address to download, or a file already on the disk.

    `overrides`: the settings chosen for this video only (spoken language, languages wanted, quality…), on top of
    those of the project (see settings.py). `facts`: what was learnt about it (duration, number of subtitles, detected
    language, steps found done on the disk, information of the site); never a choice of the user."""
    source: str
    id: str = field(default_factory=new_id)
    title: str = ""
    video: str = ""                                # the file: the source itself, or the download once finished
    selected: bool = True
    overrides: dict = field(default_factory=dict)
    stages: dict[Stage, StageStatus] = field(default_factory=dict)
    facts: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.video and not self.is_address:
            self.video = self.source
        for s in STAGES:
            self.stages.setdefault(s, StageStatus())
        if not self.is_address:
            self.stages[Stage.DOWNLOAD].state = StageState.SKIPPED

    @property
    def is_address(self) -> bool:
        return self.source.startswith(("http://", "https://"))

    def ticked(self, stage: Stage) -> bool:
        """Ticked for this step (the item must be in the work at all: `selected`, the tick of the Select screen)."""
        return self.selected and self.stages[stage].selected

    def tick(self, stage: Stage | None, on: bool):
        """Ticks or unticks the item for one step, or for every step (`stage` None: the tick of the Select screen). The
        Select tick follows: ticked as long as one step is."""
        for s in ([stage] if stage else STAGES):
            self.stages[s].selected = on
        self.selected = any(self.stages[s].selected for s in STAGES if self.stages[s].state != StageState.SKIPPED)

    def state(self, stage: Stage) -> StageState:
        return self.stages[stage].state

    def can(self, stage: Stage, event: Event) -> bool:
        """This event means something for this step as it stands."""
        return self.stages[stage].state in TRANSITIONS[event]

    def apply(self, stage: Stage, event: Event, *, task: str | None = None, error: str = "") -> bool:
        """Something happened to a step: its state follows the table (TRANSITIONS), its times too (start when it runs,
        end when it is settled). `task`: the task that now holds it; `error`: why it failed. False, and nothing
        changed, when the event means nothing in its state."""
        s = self.stages[stage]
        to = TRANSITIONS[event].get(s.state)
        if to is None:
            LOG.debug("step %s of %s: %s ignored while %s", stage.value, self.id, event.value, s.state.value)
            return False
        changed, s.state, s.error = to != s.state, to, error
        if task is not None:
            s.task = task
        if changed and to == StageState.TODO:
            s.task = None                              # to do again: the task that held it is over
        now = time.time()
        if changed and to == StageState.RUNNING:
            s.started, s.finished = now, None
        elif changed and to in (*FINISHED, StageState.ERROR, StageState.CANCELLED):
            s.finished = now
        return True

    def to_dict(self) -> dict:
        return {"id": self.id, "source": self.source, "title": self.title, "video": self.video, "selected": self.selected,
                "overrides": self.overrides, "stages": {s: self.stages[s].to_dict() for s in STAGES}, "facts": self.facts}

    @classmethod
    def from_dict(cls, d: dict) -> "Item":
        stages = {Stage(k): StageStatus.from_dict(v) for k, v in (d.get("stages") or {}).items() if k in Stage.__members__.values()}
        return cls(d["source"], d.get("id") or new_id(), d.get("title") or "", d.get("video") or "", d.get("selected", True),
                   dict(d.get("overrides") or {}), stages, dict(d.get("facts") or {}))


@dataclass
class Project:
    """A folder of work: its items in order, the steps ticked, its settings.

    `folder`: where the downloaded videos go, and the source folder when the videos come from one. `settings`: the
    choices of the project (engines, languages, destination of the files, pause before a paid translation…), on top of
    the configuration; an item may override them. `stages`: the steps ticked for at least one video, in the order they
    run (what Run runs; ProjectRun.follow_ticks keeps them so)."""
    folder: str
    items: list[Item] = field(default_factory=list)
    settings: dict = field(default_factory=dict)
    stages: list[Stage] = field(default_factory=lambda: [Stage.SUBTITLES])
    title: str = ""
    id: str = field(default_factory=new_id)
    created: float = field(default_factory=time.time)
    updated: str = ""
    status: str = ""                               # state of the last run: running, suspended, done, cancelled…
    ran: bool = False                              # a task or a download started once, even stopped since

    def __post_init__(self):
        self.stages = [s for s in STAGES if s in self.stages]          # always in the order they run
        self.untick_outside()

    def item(self, ident: str) -> Item | None:
        return next((i for i in self.items if i.id == ident), None)

    def item_by_video(self, path: str) -> Item | None:
        return next((i for i in self.items if path in (i.video, i.source)), None)

    def selected(self) -> list[Item]:
        return [i for i in self.items if i.selected]

    def wants(self, stage: Stage) -> bool:
        return stage in self.stages

    def untick_outside(self):
        """The ticks of the videos made to say what the project runs: a step that is not one of `stages` and not
        started is unticked on each video, unless a step of `stages` still needs it (the download of an address to
        subtitle, the subtitles of a video to dub). Before schema 2, a video was ticked for every step and the steps of
        the project alone said what Run ran (a project that did not dub had its videos ticked for the dubbing)."""
        for item in self.items:
            pending = [s for s in STAGES if item.state(s) not in (StageState.DONE, StageState.SKIPPED)]
            last = max((STAGES.index(s) for s in self.stages if s in pending and item.stages[s].selected), default=-1)
            needed = {s for s in pending if STAGES.index(s) <= last}
            for s in STAGES:
                if s not in self.stages and s not in needed and item.state(s) == StageState.TODO:
                    item.stages[s].selected = False

    def to_dict(self) -> dict:
        return {"schema": SCHEMA, "id": self.id, "title": self.title, "folder": self.folder, "stages": list(self.stages),
                "settings": self.settings, "created": self.created, "updated": self.updated, "status": self.status, "ran": self.ran,
                "items": [i.to_dict() for i in self.items]}

    RUN_KEYS = ("ran", "gpu_released", "gpu_restore", "gpu_wait", "gpu_wait_items")

    @classmethod
    def from_dict(cls, d: dict) -> "Project":
        settings = dict(d.get("settings") or {})
        old = {k: settings.pop(k) for k in cls.RUN_KEYS if k in settings}     # written among the settings by older versions
        project = cls(d["folder"], [Item.from_dict(i) for i in d.get("items") or []], settings,
                      [Stage(s) for s in d.get("stages", [Stage.SUBTITLES]) or [] if s in Stage.__members__.values()],
                      d.get("title") or "", d.get("id") or new_id(), d.get("created") or time.time(), d.get("updated") or "",
                      d.get("status") or "", bool(d.get("ran") or old.get("ran")))
        return project
