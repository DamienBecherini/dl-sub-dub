"""A project being worked on: the project (saved in its file) plus what only lives while the service runs (state of
the run, phase, measured speeds, number of videos at once).

The work goes phase by phase, because the cost of every translation is shown before any of them
starts: phase 1 transcribes every video (and downloads those that are addresses, at the same time), phase 2
translates them after "Translate all" (several at once through a remote API), phase 3 dubs them, one at a time on the
graphics card. A video taken to the end on its own ("Continue") skips the phases.

For the command line and the summary of a project, each item is also shown as a "video" (`videos`, `view`): one
state among todo, running, prepared, translated, done, error, cancelled, worked out from the states of its steps.
"""
import threading
import time
from collections import ChainMap
from dataclasses import dataclass, field
from pathlib import Path

from .. import config, dubbing, estimation, gpu, i18n, review, steps
from ..project import Event, Item, Project, Stage, StageState, rules, settings, stages, store
from ..states import RunState, VideoState
from ..project.choices import stop_for
from . import timing

S = StageState


class Changes:
    """"This project changed", told to whoever waits for it (the stream of each page open on the project): a counter
    that only grows, and a condition woken at each change. A listener keeps the count it last saw: a change made
    while it was busy sending is not lost."""

    def __init__(self):
        self._woken = threading.Condition()
        self.count = 0

    def tell(self):
        with self._woken:
            self.count += 1
            self._woken.notify_all()

    def wait(self, seen: int, timeout: float) -> int:
        """Waits for a change after the count `seen`, `timeout` seconds at most; returns the count now."""
        with self._woken:
            self._woken.wait_for(lambda: self.count != seen, timeout)
            return self.count


@dataclass
class CardShare:
    """The graphics card between a local translation model and the dubbing, while this project is open (see gpu.py
    and card.ready). Never written in the project file: after a restart, the card is looked at again."""
    released: bool = False                     # the model left the card: the dubbing may take it
    restore: bool = False                      # the model was stopped by the tool: started again once the dubbing is over
    wait: bool = False                         # the project waits for the user to free the card
    waiting: list[str] = field(default_factory=list)      # lines run on their own that wait for the same

    @property
    def asked(self) -> bool:
        """The user is asked to free the card."""
        return self.wait or bool(self.waiting)


class ProjectRun:
    def __init__(self, project: Project, file: Path):
        self.project = project
        self.file = Path(file)
        self.state = RunState.RUNNING
        self.phase = 1
        self.finished = None
        self.after_save = None                 # manager callback (remembers the last project for the next session)
        self.step_of = None                    # manager callback: current step of a task (id → "transcription"…)
        self.progress_of = None                # manager callback: where the download of an item stands (dlprogress)
        self.work_of = None                    # manager callback: where the task of an item stands (step, progress, time left)
        self.passes: dict[int, list[tuple[float, float]]] = {}   # phase → [(weight, seconds)] of the processed videos
        self.files_checked = 0.0               # when the files of the videos were last looked for (see check_files)
        self.concurrency = 1                   # videos processed at once (several: translation by a remote API)
        self.active: list[Stage] | None = None # steps run now ("Run this step"); None: those ticked on the diagram
        self.card = CardShare()
        self.review_stamp = 0                  # raised when proposals come from outside (an assistant) or sure fixes were applied without a question: the page reads them again
        self.held: set[str] = set()            # videos paused again after a go-ahead (the proposals of their AI review): they wait for the next one
        self.finishing: set[str] = set()       # videos at work when the project was paused "after the running videos": they end (see rules.alone)
        self.agreed: set[str] = set()          # videos that had the go-ahead of the project and wait for their turn (phase 2): not for the user (see rules.asks)
        self.changes = Changes()               # told at each save, each progress of a task or of a download
        self.lock = threading.RLock()          # the parallel tasks and the downloads update the project and its file

    # --- shortcuts to the project ---
    @property
    def id(self) -> str:
        return self.project.id

    @property
    def root(self) -> str:
        return self.project.folder

    @property
    def options(self) -> dict:
        """The settings of the project; changing them changes the project."""
        return self.project.settings

    @property
    def created(self) -> float:
        return self.project.created

    def settings(self, item: Item | None = None, cfg: dict | None = None) -> ChainMap:
        return settings.resolve(cfg if cfg is not None else config.load(), self.project, item)

    def items(self) -> list[Item]:
        return self.project.selected()

    def subtitle_settings(self, cfg: dict | None = None) -> dict:
        """The settings of the Subtitles step as they will apply: the engines chosen, otherwise the active ones; whether a
        free translation waits too (`pause`; a paid model always does, unless `spend_agreed`); whether the sure fixes of
        the AI review are applied without a question (`review_auto`)."""
        cfg = cfg if cfg is not None else config.load()
        s = self.settings(None, cfg)
        try:
            translator = config.engine(cfg, "translators", "active_translator", s["translator"] or None)
        except config.ConfigError:
            translator = {}
        transcription = s["transcription"] or cfg.get("active_transcription") or ""
        reviewer = review.engine(cfg, s["translator"] or None, s["review_engine"] or None)
        return {"transcription": transcription, "translator": translator.get("id") or s["translator"] or "",
                "translator_paid": bool(translator) and estimation.pause_by_default(translator),
                "languages": list(s["languages"] or []), "language": s["language"] or "",
                "pause": s["pause"] is True, "review": review.mode(s["review"]), "review_engine": s["review_engine"] or "",
                "reviewer": (reviewer or {}).get("id") or "", "reviewer_paid": bool(reviewer) and estimation.pause_by_default(reviewer),
                "review_auto": bool(s["review_auto"]), "spend_agreed": bool(s["spend_agreed"]),
                "destination": dict(s["destination"])}

    def dubbing_settings(self, cfg: dict | None = None) -> dict:
        """The settings of the Dubbing step: the engine of the configuration (available or not), the dubbed language
        chosen ("" = the first one translated), the MKV with every track or the dubbed track alone, the MKV in place of
        the original video."""
        try:
            cfg = cfg if cfg is not None else config.load()
            available = dubbing.available(cfg)
        except config.ConfigError:
            cfg, available = cfg or {}, False
        d = cfg.get("dubbing") or {}
        s = self.settings(None, cfg) if cfg else {}
        mkv = s.get("mkv") if s.get("mkv") is not None else self.options.get("stop") != "dubbing"
        try:
            translator = config.engine(cfg, "translators", "active_translator", self.options.get("translator") or None) if cfg else None
        except config.ConfigError:
            translator = None
        card = {"setting": d.get("gpu_sharing") or "auto", "sharing": gpu.sharing(cfg) if cfg else "one_at_a_time",
                "local": gpu.is_local(translator), "free": gpu.how_to_free(cfg, translator) if cfg else "",
                "translator": (translator or {}).get("name") or (translator or {}).get("id") or "",
                "test": d.get("gpu_test"), "hooks": bool((d.get("free_gpu") or {}).get("stop")),
                "wait": self.card.asked}
        return {"available": available, "enabled": bool(d.get("enabled")), "model": d.get("model") or "", "gpu": card,
                "steps": d.get("steps"), "language": (steps.dub_codes(s.get("dub_language")) or [""])[0],
                "languages": steps.dub_codes(s.get("dub_language")), "mkv": bool(mkv),
                "replace": bool(s.get("replace"))}

    def context(self, cfg: dict | None = None) -> rules.Context:
        """What the rules of the project need to know of this run and of this machine (see project/rules.py)."""
        try:
            cfg = cfg if cfg is not None else config.load()
            available, sharing = dubbing.available(cfg), gpu.sharing(cfg)
        except config.ConfigError:
            cfg, available, sharing = cfg or {}, False, ""
        resolved: dict = {}

        def of(item: Item | None):
            key = item.id if item is not None else ""
            if key not in resolved:
                resolved[key] = settings.resolve(cfg, self.project, item)
            return resolved[key]
        self.finishing &= {i.id for i in self.project.items if any(i.state(s) == S.RUNNING for s in i.stages)}     # ended: no longer
        self.agreed &= {i.id for i in self.project.items if i.state(Stage.SUBTITLES) == S.PAUSED}                  # taken: no longer
        return rules.Context(of, available, str(self.state), self.started(), self.project.launched, sharing,
                             self.options.get("language") or "", frozenset(self.finishing), frozenset(self.agreed - self.held))

    def follow_ticks(self):
        """The steps of the project are the ones ticked for at least one video (rules.ticked_steps), and how far the
        work goes follows: to the MKV when a video is to dub. Called after every change of the ticks."""
        if self.project.items:                 # an empty project keeps the steps it was made with, for its first videos
            self.project.stages = rules.ticked_steps(self.project)
        stop = stop_for(self.project.stages, self.options.get("stop"), self.options.get("mkv"))
        if self.options.get("stop") or stop != "translation":       # "translation", the default, is not written
            self.options["stop"] = stop

    def wants(self, stage: Stage) -> bool:
        """The step is run now: one of the steps ticked on the diagram, or the one step started from its screen."""
        return stage in (self.active or self.project.stages)

    def takes(self, item: Item, stage: Stage) -> bool:
        """This item goes through this step in the current run. Dubbing alone never starts the subtitles of a video
        (a translation may be paid): only the videos already subtitled and translated are dubbed."""
        return self.wants(stage) and item.ticked(stage)

    def wants_dubbing(self) -> bool:
        if not steps.goes_to(self.options.get("stop"), "dubbing"):
            return False
        try:
            return dubbing.available(config.load())
        except config.ConfigError:
            return False

    def video_state(self, item: Item, dub: bool | None = None) -> VideoState:
        dl, sub, dub_state = item.state(Stage.DOWNLOAD), item.state(Stage.SUBTITLES), item.state(Stage.DUBBING)
        both = (dl, sub, dub_state)
        if S.RUNNING in both:
            return VideoState.RUNNING
        if sub == S.PAUSED:
            return VideoState.PREPARED
        if S.ERROR in both:
            return VideoState.ERROR
        if S.CANCELLED in both:
            return VideoState.CANCELLED
        if sub == S.DONE:
            wanted = self.wants_dubbing() if dub is None else dub
            return VideoState.TRANSLATED if wanted and dub_state == S.TODO else VideoState.DONE
        return VideoState.TODO

    def view(self, item: Item, dub: bool | None = None) -> dict:
        """An item as one video with one state (command line, summary of the project)."""
        running = next((item.stages[s] for s in (Stage.DUBBING, Stage.SUBTITLES, Stage.DOWNLOAD) if item.state(s) in (S.RUNNING, S.PAUSED)), None)
        last = running or next((item.stages[s] for s in (Stage.DUBBING, Stage.SUBTITLES, Stage.DOWNLOAD) if item.stages[s].task), None)
        error = next((item.stages[s].error for s in (Stage.DOWNLOAD, Stage.SUBTITLES, Stage.DUBBING) if item.state(s) == S.ERROR), "")
        path = item.video or item.source
        return {"id": item.id, "path": path, "source": item.source, "name": item.title or Path(path).name,
                "language": item.overrides.get("language") or "", "state": self.video_state(item, dub),
                "task": last.task if last else None, "started": running.started if running else None, "error": error,
                **{k: item.facts.get(k) for k in ("count", "detected_language", "duration", "transcribed", "reviewed",
                                                  "validated", "translated", "dubbed", "muxed")},
                "stages": {s: item.state(s) for s in (Stage.DOWNLOAD, Stage.SUBTITLES, Stage.DUBBING)}}

    @property
    def videos(self) -> list[dict]:
        dub = self.wants_dubbing()
        return [self.view(i, dub) for i in self.items()]

    def to_dict(self) -> dict:
        return {"id": self.id, "root": self.root, "options": self.options, "state": self.state, "phase": self.phase,
                "created": self.created, "finished": self.finished, "file": str(self.file).replace("\\", "/"),
                "stages": list(self.project.stages), "title": self.project.title, "videos": self.videos,
                "started": self.started()}

    def started(self) -> bool:
        """Some work was done or is under way (a step left "to do", or a task or a download started once, even stopped
        since): the project can no longer move to another folder, and it is resumed rather than run."""
        return self.state == RunState.RUNNING or self.project.ran or any(
            i.state(s) not in (S.TODO, S.SKIPPED) for i in self.project.items for s in i.stages)

    FILES_CHECK_S = 5

    def check_files(self):
        """A video whose file is no longer there (deleted, moved, disk unplugged) says so on its line:
        facts["missing"]. Looked for every few seconds at most: the summary is asked for all the time."""
        if time.monotonic() - self.files_checked < self.FILES_CHECK_S:
            return
        self.files_checked = time.monotonic()
        for item in self.project.items:
            gone = bool(item.video) and item.state(Stage.DOWNLOAD) != S.RUNNING and not Path(item.video).is_file()
            if gone:
                item.facts["missing"] = True
            else:
                item.facts.pop("missing", None)

    def summary(self) -> dict:
        self.check_files()
        d = self.to_dict()
        ctx = self.context()
        counts = {e: sum(1 for v in d["videos"] if v["state"] == e) for e in VideoState}
        measures = self.passes.get(self.phase, [])[-10:]
        current = self.current()
        step = (self.step_of(current.stages[Stage.SUBTITLES].task or current.stages[Stage.DUBBING].task) or "") if current and self.step_of else ""
        if current and not step and current.state(Stage.DOWNLOAD) == S.RUNNING:
            step = "download"
        return {**d, "order": "videos" if self.by_video else "steps", "counts": counts, "step": step, "total": len(d["videos"]), "concurrency": self.concurrency,
                "review_stamp": self.review_stamp,
                "avg_duration_s": round(sum(s for _, s in measures) / len(measures)) if measures else None,
                "current": (current.video or current.source) if current else None,
                "remaining_s": self.remaining_s(timing.known_rate(self.phase, self.options) if self.state == RunState.RUNNING else None),
                "dubbing_s": self.dubbing_left_s(), "rates": self.plan_rates(), "blocked": [i.id for i in rules.blocked(self.project, self.active)],
                "rules": rules.project_view(ctx, self.project),
                "active": list(self.active or self.project.stages), "subtitles": self.subtitle_settings(),
                "dubbing": self.dubbing_settings(),
                "items": [rules.item_view(ctx, self.project, i) for i in self.project.items],
                "downloads": {i.id: d for i in self.project.items if i.state(Stage.DOWNLOAD) == S.RUNNING and self.progress_of
                              and (d := self.progress_of(i.id)) is not None},
                "work": {i.id: w for i in self.project.items for s in (Stage.SUBTITLES, Stage.DUBBING)
                         if i.state(s) == S.RUNNING and self.work_of and (w := self.work_of(i.stages[s].task)) is not None}}           # every item, ticked or not (step Select)

    def save(self):
        with self.lock:
            self.project.status = self.state
            try:
                store.save(self.project, self.file)
            except OSError:
                pass
            if self.after_save:
                self.after_save(self)
        self.changes.tell()

    # --- progress ---
    def waiting_in_phase(self, item: Item) -> bool:
        """The item is what the current phase takes care of."""
        sub, dub = item.state(Stage.SUBTITLES), item.state(Stage.DUBBING)
        if self.phase in (1, 2):
            waits = self.takes(item, Stage.SUBTITLES) and (sub == S.TODO if self.phase == 1 else sub == S.PAUSED and item.id not in self.held)
            return waits or (self.by_video and self.phase == 1 and sub == S.DONE and item.state(Stage.DUBBING) == S.TODO
                             and self.takes(item, Stage.DUBBING) and self.wants_dubbing())
        return sub == S.DONE and dub == S.TODO and self.takes(item, Stage.DUBBING)

    @property
    def by_video(self) -> bool:
        """Each video to its end before the next one (setting `order`), rather than step by step."""
        return self.options.get("order") == "videos"

    def next_item(self) -> Item | None:
        """Next item for the current phase, once its video is there. In phases 2 and 3, a video set back to do
        ("Retry") starts again from the transcription. Video by video: the first video, in the order
        of the list, with a step left (its dubbing once subtitled, otherwise its subtitles)."""
        ready = lambda i: stages.STRATEGIES[Stage.SUBTITLES].can_start(i)            # noqa: E731
        if self.by_video and self.phase == 1:
            if any(i.state(Stage.SUBTITLES) == S.PAUSED and self.takes(i, Stage.SUBTITLES) for i in self.items()):
                return None                    # a video waits for the user (a cost, the proposals of its review): the next ones after it
            dub = stages.STRATEGIES[Stage.DUBBING]
            for i in self.items():
                if self.wants_dubbing() and i.state(Stage.DUBBING) == S.TODO and self.takes(i, Stage.DUBBING) and dub.can_start(i):
                    return i
                if i.state(Stage.SUBTITLES) == S.TODO and self.takes(i, Stage.SUBTITLES) and ready(i):
                    return i
            return None
        to_process = [i for i in self.items() if self.waiting_in_phase(i) and ready(i)] \
            or [i for i in self.items() if i.state(Stage.SUBTITLES) == S.TODO and self.takes(i, Stage.SUBTITLES) and ready(i)]
        # Resume: the transcriptions already done go first (a few seconds each), the counter is quickly right.
        return next((i for i in to_process if i.state(Stage.SUBTITLES) == S.TODO and i.facts.get("transcribed")),
                    to_process[0] if to_process else None)

    def downloads_left(self) -> bool:
        """Addresses still to download: phase 1 is not over before them, their videos have to be transcribed."""
        return any(i.is_address and self.takes(i, Stage.DOWNLOAD) and i.state(Stage.DOWNLOAD) in (S.TODO, S.RUNNING) for i in self.items())

    def current(self) -> Item | None:
        return next((i for i in self.items() if S.RUNNING in (i.state(s) for s in (Stage.DOWNLOAD, Stage.SUBTITLES, Stage.DUBBING))), None)

    def item_of_task(self, task_id: str) -> Item | None:
        return next((i for i in self.project.items if any(st.task == task_id for st in i.stages.values())), None)

    def weight(self, item: Item) -> float:
        """What makes the duration of a video vary in this phase (see timing.py); 0 if still unknown."""
        if self.phase in (1, 3):
            return float(item.facts.get("duration") or 0)
        return float(item.facts.get("count") or 0) * max(1, len(self.options.get("languages") or []))

    def record_pass(self, weight: float, seconds: float):
        self.passes.setdefault(self.phase, []).append((weight, seconds))

    def _running_in_phase(self, item: Item) -> bool:
        stage = Stage.DUBBING if self.phase == 3 else Stage.SUBTITLES
        return item.state(stage) == S.RUNNING

    def remaining_s(self, known_rate: float | None = None, now: float | None = None) -> int | None:
        """Seconds before the end of the current phase, from the last 10 processed videos, otherwise `known_rate`.
        None if the run is not working or if nothing allows an estimate."""
        if self.state != RunState.RUNNING:
            return None
        to_transcribe = lambda i: self.phase != 1 or not i.facts.get("transcribed")  # noqa: E731 - a resume costs nothing
        upcoming = [i for i in self.items() if self.waiting_in_phase(i) and to_transcribe(i)]
        running = [i for i in self.items() if self._running_in_phase(i) and to_transcribe(i)]
        measures = self.passes.get(self.phase, [])[-10:]
        measured_weight = sum(p for p, _ in measures)
        rate = sum(s for _, s in measures) / measured_weight if measured_weight > 0 else known_rate
        known = [p for p in map(self.weight, upcoming + running) if p > 0]
        if not rate or not known:
            return None
        mean = sum(known) / len(known)
        left = sum(self.weight(i) or mean for i in upcoming) * rate
        for i in running:
            stage = Stage.DUBBING if self.phase == 3 else Stage.SUBTITLES
            now_s = time.time() if now is None else now
            started = i.stages[stage].started
            elapsed = now_s - (started if started is not None else now_s)
            left += max(0.0, (self.weight(i) or mean) * rate - elapsed)
        return round(left / max(1, self.concurrency))

    def progress_text(self) -> str:
        """Short text for the Local service module: "video 3/22", running video included."""
        items = self.items()
        rank = max(1, len(items) - sum(1 for i in items if self.waiting_in_phase(i)))
        suffix = {1: "", 2: " (" + i18n.t("step.translation").lower() + ")", 3: " (" + i18n.t("step.dubbing").lower() + ")"}[self.phase]
        return i18n.t("activity.video", rank=rank, total=len(items)) + suffix

    def plan_rates(self) -> dict:
        """Seconds of computing per second of video for each step, to estimate a run before it starts (the page sums
        them over the videos ticked): the subtitles (the transcription, with the translation when it is done apart,
        after a pause), the dubbing. None: nothing measured nor announced in the settings."""
        o = self.options
        sub = timing.known_rate(1, o)
        languages = o.get("languages") or []
        if sub and o.get("pause", True) is not False and languages:
            per_line = timing.known_rate(2, o)
            if per_line:
                sub += timing.LINES_PER_SECOND * per_line * len(languages)
        return {"subtitles": round(sub, 4) if sub else None, "dubbing": timing.known_rate(3, o)}

    def dubbing_left_s(self) -> int:
        """Graphics card time for the videos still to dub; 0 if this project only wants subtitles."""
        if not steps.goes_to(self.options.get("stop"), "dubbing"):
            return 0
        todo = [i for i in self.items() if not i.facts.get("muxed") and S.ERROR not in (i.state(Stage.SUBTITLES), i.state(Stage.DUBBING))
                and S.CANCELLED not in (i.state(Stage.SUBTITLES), i.state(Stage.DUBBING))]
        return round(sum(float(i.facts.get("duration") or 0) for i in todo) * (timing.known_rate(3, self.options) or 0))

    def settle(self):
        """A project waiting for the go-ahead where no video waits any more (decided, translated or cancelled on its own,
        from its line) goes on: its go-ahead would accept nothing. The feeding ends the phase if nothing is left."""
        if self.state == RunState.AWAITING_TRANSLATION and not rules.waiting(self.project):
            self.state = RunState.RUNNING

    def end_phase(self):
        """Nothing more to start in the current phase."""
        if self.downloads_left():
            return                             # their videos come to phase 1 when downloaded
        items = self.items()
        if any(i.state(Stage.SUBTITLES) == S.PAUSED for i in items):     # after phase 1: paused again (see `held`), or a video put back to do
            self.state = RunState.AWAITING_TRANSLATION
        elif self.phase < 3 and any(self.video_state(i) == VideoState.TRANSLATED for i in items):
            self.phase, self.state = 3, RunState.RUNNING
        else:
            self.state, self.finished = RunState.DONE, time.time()

    @classmethod
    def from_file(cls, file: Path) -> "ProjectRun":
        """Project read back for a resume: what was not finished starts again, errors and cancellations included (fast:
        the transcription is kept). An old batch file is saved as a project next to it."""
        file = Path(file)
        project = store.load(file)
        for item in project.items:
            for stage in item.stages:
                item.apply(stage, Event.REOPEN)
        if file.name != store.FILE_NAME:
            file = file.with_name(store.FILE_NAME)
        run = cls(project, file)
        run.follow_ticks()
        left = any(item.ticked(s) and item.state(s) not in (S.DONE, S.SKIPPED) for item in project.selected() for s in project.stages)
        run.state = RunState.SUSPENDED if left else RunState.DONE      # a finished project opened again stays finished
        return run
