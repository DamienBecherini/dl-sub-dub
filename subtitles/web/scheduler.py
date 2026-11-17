"""The scheduling of the projects at work: at each turn of the queue's loop, the next videos of each running project
(one task per video and per phase, see runs.py), one step of one video run now (the buttons of a line, the chains of
its ▾), and what the end of a task does to the steps of its video.

The scheduler listens to the task queue (`tasks.QueueEvents`, Observer): each change of a task updates the steps of its
item (`Item.apply`) and the project file. The orchestrator (orchestrator.py) builds the commands of a project on it.
"""
import threading
from pathlib import Path

from .. import config, dubbing, glossary, i18n, review, steps, translation
from ..project import STAGES, Event, Item, Stage, StageState, rules
from ..project.choices import languages_of, own, until_of
from ..states import ACTIVE_TASK_STATES, RunState, TaskState
from . import card, observed
from .fetching import Fetching
from .runs import ProjectRun
from .tasks import Task, TaskQueue

S = StageState
E = Event


class Scheduler:
    def __init__(self, queue: TaskQueue):
        self.queue = queue
        self.all: dict[str, ProjectRun] = {}
        self.lock = threading.Lock()
        self.fetching = Fetching(self.all, queue.wake.set, self._follow)
        queue.events = self

    def start_feeding(self):
        """The downloads and the reading of the sites, fed by a thread of their own (see Fetching)."""
        self.fetching.start()

    def feed(self):
        """Starts the downloads, then the next videos of each running project, up to the number allowed at once."""
        for run in list(self.all.values()):
            if run.state != RunState.CANCELLED:
                self.fetching.feed_infos(run)
            if run.state == RunState.RUNNING:
                with run.lock:
                    self.fetching.feed_downloads(run)
                    self._feed(run)

    def _concurrency(self, run: ProjectRun) -> int:
        if run.phase != 2:
            return 1
        try:
            return translation.concurrency(config.engine(config.load(), "translators", "active_translator", run.options.get("translator") or None))
        except config.ConfigError:
            return 1

    def _feed(self, run: ProjectRun):
        run.concurrency = self._concurrency(run)

        def active(item):
            return any(item.state(s) == S.RUNNING and (t := self.queue.get(item.stages[s].task or "")) is not None
                       and t.state in ACTIVE_TASK_STATES for s in (Stage.SUBTITLES, Stage.DUBBING))
        actives = sum(1 for i in run.items() if active(i))
        for _ in range(run.concurrency - actives):
            item = run.next_item()
            if item is None:
                if actives == 0:               # the parallel videos still on their way will end the phase
                    run.end_phase()
                    if run.state == RunState.DONE:
                        card.give_back(run)
                    run.save()
                return
            o = run.options
            sub = item.stages[Stage.SUBTITLES]
            if sub.state == S.TODO:
                # Whatever the phase and the order of the work, its task stops before a paid model (the AI review,
                # the translation; see TaskQueue._pauses_after_preparation) and waits for the proposals of the review
                # to be decided.
                t = self._add_task(run, item, pause=o.get("pause"), check_names=True)
                item.apply(Stage.SUBTITLES, E.START, task=t.id)
            elif sub.state == S.DONE:          # phase 3 (or video by video, right after its subtitles): the dubbing
                if not card.ready(run, wait=True):
                    return                         # the project waits for the card (run.card.wait)
                t = self._add_task(run, item, pause=False, until=o.get("stop"), review=False)
                item.apply(Stage.DUBBING, E.START, task=t.id)
            else:                              # phase 2: the translation of a paused task
                t = self.queue.resume(sub.task, own(o, item, "translator"), self._languages(run, item), self._review_choice(run, item))
                if t is None:
                    item.apply(Stage.SUBTITLES, E.FAIL, error=i18n.t("err.paused_task_missing"))
                    continue
                item.apply(Stage.SUBTITLES, E.START, task=t.id)
            actives += 1
            run.save()

    # --- one task: one video, one pass of the chain ---
    def _add_task(self, run: ProjectRun, item: Item, continued: bool = False, **options) -> Task:
        """A task for a video of the project. `continued`: it goes through several steps at once (see Task)."""
        o = run.options
        run.project.ran = True                 # work started once (see ProjectRun.started)
        if not steps.goes_to(options.get("until"), "dubbing"):      # a translation: the model needs the card again
            card.give_back(run, now=True)
        return self.queue.add({"source": item.video, "transcription": own(o, item, "transcription") or "",
                               "translator": own(o, item, "translator") or "",
                               "language": item.overrides.get("language") or o.get("language") or "",
                               "languages": self._languages(run, item), "output_dir": o.get("output_dir") or "",
                               "destination": o.get("destination"),
                               "redo": bool(o.get("redo")) and item.state(Stage.SUBTITLES) == S.TODO, "project": run.id,
                               "video_duration": item.facts.get("duration") or 0, "glossary": str(glossary.path_for(run.file)), "project_file": str(run.file),
                               **self._review_choice(run, item), "pause": False,
                               "dub_language": item.overrides.get("dub_language") or o.get("dub_language") or "",
                               "replace": bool(item.overrides["replace"] if item.overrides.get("replace") is not None else o.get("replace")),
                               **options, **({"until": until_of(item, options["until"])} if "until" in options else {})}, continued)

    @staticmethod
    def _review_choice(run: ProjectRun, item: Item | None = None) -> dict:
        """The AI review of a video for its task: which one ("", "names", "full": its own choice, "none" included,
        otherwise the project's), the model chosen for it ("": see review.engine), and whether the user said not to be
        asked before a paid model any more in this project."""
        o = run.options
        return {"review": review.mode(own(o, item, "review") if item is not None else o.get("review")),
                "review_engine": o.get("review_engine") or "", "spend_agreed": bool(o.get("spend_agreed"))}

    @staticmethod
    def _languages(run: ProjectRun, item: Item) -> list[str] | None:
        """The languages of the subtitles of a video for its task (see choices.languages_of)."""
        return languages_of(run.options, item, lambda: config.load().get("target_languages"))

    def _left_to_dub(self, run: ProjectRun, t: Task) -> bool:
        """Translated, and the project asks to go further: the video waits for its turn on the graphics card (phase 3)."""
        try:
            possible = dubbing.available(config.load())
        except config.ConfigError:
            possible = False
        return possible and steps.goes_to(run.options.get("stop"), "dubbing") and not steps.goes_to(t.options.get("until"), "dubbing")

    def _task_step(self, ident: str | None) -> str:
        t = self.queue.get(ident or "")
        return t.step if t and t.state == TaskState.RUNNING else ""

    def _task_work(self, ident: str | None) -> dict | None:
        """Where the task of a video stands: its step, the subtitles translated or the sentences dubbed so far, the
        seconds left, and which voice of how many when several languages are dubbed."""
        t = self.queue.get(ident or "")
        if not t or t.state not in ACTIVE_TASK_STATES:
            return None
        p = t.current_progress()
        return {"step": t.step, "progress": p and {k: p[k] for k in ("done", "total")}, "remaining_s": t.remaining_s(),
                "queued": t.state == TaskState.QUEUED, **({"voice": p["voice"]} if p and p.get("voice") else {})}

    # --- one item, one step: the buttons of a line ---
    def run_item(self, ident: str, item_id: str, stage: Stage) -> bool:
        """Runs one step for one video, now, whatever the state of the project (suspended or finished included): Run,
        Retry after an error or a cancellation, and Translate when its translation waits for the go-ahead. The
        subtitles still stop before a paid translation if the project says so; the item is ticked for the step."""
        run = self.all.get(ident)
        item = run.project.item(item_id) if run else None
        if item is None or not rules.can_run(run.context(), item, stage):
            return False                       # cancelled project, step at work, or lacking something (rules.lacks)
        o = run.options
        with run.lock:
            status = item.stages[stage]
            if stage == Stage.DOWNLOAD:
                if item.id in self.fetching.downloads:
                    return False
                item.tick(stage, True)
                status.task, status.error = None, ""
                self.fetching.start_download(run, item, config.load())
                return True
            task = self.queue.get(status.task or "")
            if stage == Stage.SUBTITLES and status.state == S.PAUSED and task and task.state == TaskState.PAUSED:
                self.queue.resume(task.id, own(o, item, "translator"), self._languages(run, item), self._review_choice(run, item))      # Translate
            elif stage == Stage.SUBTITLES:
                task = self._add_task(run, item, pause=o.get("pause"), check_names=True, continued=True)
            else:
                if not card.ready(run, wait=False):          # waits for the user to free the card
                    if item.id not in run.card.waiting:
                        run.card.waiting.append(item.id)
                    run.save()
                    return True
                stop = o.get("stop") if o.get("stop") in ("dubbing", "mux") else "mux"
                task = self._add_task(run, item, until=stop, pause=False, review=False, continued=True)
            item.tick(stage, True)
            run.follow_ticks()
            if item.state(stage) == S.SKIPPED:
                item.apply(stage, E.RESET)             # left out earlier (nothing was translated to dub): it applies now
            item.apply(stage, E.START, task=task.id)
            run.save()
        self.queue.wake.set()
        return True

    def run_chain(self, ident: str, item_id: str, chain: list[Stage]) -> bool:
        """Several steps of one video, one after the other (the ▾ menu: Download + Subtitle + Dub…): the first now, as
        with its own button; the others kept in the video (facts["then"]) and started as each step ends."""
        run = self.all.get(ident)
        item = run.project.item(item_id) if run else None
        if item is None or not chain:
            return False
        with run.lock:
            item.facts["then"] = [s.value for s in chain[1:]]
            for s in chain:
                item.tick(s, True)
            for s in chain[1:]:                    # asked again: a step done is made again (its subtitles may have changed)
                if item.state(s) == S.DONE:
                    item.apply(s, E.RESET)
            run.follow_ticks()
            run.save()
        if self.run_item(ident, item_id, chain[0]):
            return True
        with run.lock:
            item.facts.pop("then", None)
            run.save()
        return False

    def _follow(self, run: ProjectRun, item_id: str):
        """A step of a video ended: the next step of its chain starts (one already running, started by the project,
        is waited for). A step that failed or was cancelled ends the chain."""
        with run.lock:
            item = run.project.item(item_id)
            then = list((item.facts.get("then") or []) if item else [])
            if not then:
                return
            then = [s for s in then if item.state(Stage(s)) != S.DONE]
            nxt = Stage(then[0]) if then else None
            before = [s for s in STAGES if nxt and STAGES.index(s) < STAGES.index(nxt)]
            if not then or any(item.state(s) in (S.ERROR, S.CANCELLED) for s in before):
                item.facts.pop("then", None)       # all done, or a step before the next one failed or was cancelled
                run.save()
                return
            if any(item.state(s) in (S.RUNNING, S.PAUSED) for s in [*before, nxt]):
                item.facts["then"] = then          # still at work (or its translation waits): later
                run.save()
                return
            item.facts["then"] = then[1:]
            run.save()
        if not self.run_item(run.id, item_id, nxt):
            with run.lock:
                item.facts.pop("then", None)
                run.save()

    def cancel_item(self, ident: str, item_id: str, stage: Stage) -> bool:
        """Cancels one step of one video (its download, or its task, running or waiting before its translation)."""
        run = self.all.get(ident)
        item = run.project.item(item_id) if run else None
        if item is None:
            return False
        if stage == Stage.DOWNLOAD:
            return self.fetching.stop(item.id)
        status = item.stages[stage]
        if status.state not in (S.RUNNING, S.PAUSED) or not status.task:
            return False
        return self.queue.cancel(status.task)

    # --- events of the queue (tasks.QueueEvents) ---
    def _run_of(self, t: Task) -> ProjectRun | None:
        return self.all.get(t.options.get("project") or "")

    def runs_in_parallel(self, t: Task) -> bool:
        """Translations of a project by a remote API; never the dubbing (a single graphics card)."""
        run = self._run_of(t)
        return bool(run and run.concurrency > 1 and t.prepared is not None and not steps.goes_to(t.options.get("until"), "dubbing"))

    def replaces_original(self, t: Task) -> bool:
        """The MKV takes the place of the video: the video's own choice, otherwise the project's (in the task)."""
        if "replace" in t.options:
            return bool(t.options["replace"])
        run = self._run_of(t)
        return bool((run.options if run else t.options).get("replace"))

    def pass_measured(self, t: Task, weight: float, seconds: float):
        run = self._run_of(t)
        if run:
            run.record_pass(weight, seconds)

    def tick(self):
        self.feed()

    def task_progressed(self, t: Task):
        run = self._run_of(t)
        if run:
            run.changes.tell()

    def settings_changed(self):
        """The settings of the service changed (an engine, the dubbing set up): what each project can do may have too."""
        for run in list(self.all.values()):
            run.changes.tell()

    @staticmethod
    def _finished(item: Item, stage: Stage, task: str | None = None):
        """The task of a step says its work is made. A step still running finishes; one waiting for its go-ahead ran
        meanwhile; one settled otherwise in between (cancelled with its project, put back by a Stop) has its work on
        the disk all the same: found done."""
        if item.state(stage) == S.PAUSED:
            item.apply(stage, E.START)
        item.apply(stage, E.FINISH if item.state(stage) == S.RUNNING else E.FOUND_DONE, task=task)

    def task_changed(self, t: Task):
        run = self._run_of(t)
        item = run.item_of_task(t.id) if run else None
        if not item:
            return
        with run.lock:
            dubbing_pass = item.stages[Stage.DUBBING].task == t.id and item.state(Stage.SUBTITLES) == S.DONE
            stage = Stage.DUBBING if dubbing_pass else Stage.SUBTITLES
            if t.state == TaskState.PAUSED:
                item.apply(Stage.SUBTITLES, E.WAIT)
                if run.phase == 2 and not run.by_video:      # paused again after its go-ahead (the proposals of its review): until the next one
                    run.held.add(item.id)
            elif t.state == TaskState.DONE:
                self._finished(item, Stage.SUBTITLES)
                if steps.goes_to(t.options.get("until"), "dubbing") and (t.result or {}).get("not_dubbed"):
                    if item.state(Stage.DUBBING) == S.DONE:
                        item.apply(Stage.DUBBING, E.RESET)                                       # (written done by an older project)
                    item.apply(Stage.DUBBING, E.FAIL, task=t.id, error=t.result["not_dubbed"])   # nothing translated to dub
                elif steps.goes_to(t.options.get("until"), "dubbing"):
                    self._finished(item, Stage.DUBBING, t.id)
                elif not self._left_to_dub(run, t) and item.state(Stage.DUBBING) == S.RUNNING:
                    item.apply(Stage.DUBBING, E.PUT_BACK)
            elif t.state == TaskState.ERROR:
                item.apply(stage, E.FAIL, error=t.error)
            elif t.state == TaskState.CANCELLED and t.stopped:
                # "Stop": back in the list; the dubbing pass waits for its turn again, the rest starts again from the
                # transcription, which is kept (and so are the translations and the separated voice). A project
                # cancelled since keeps its steps cancelled: the event means nothing there.
                item.apply(stage, E.PUT_BACK)
            elif t.state == TaskState.CANCELLED:
                item.apply(stage, E.CANCEL)
            if t.state != TaskState.RUNNING and item.state(Stage.DUBBING) == S.RUNNING and stage == Stage.SUBTITLES:
                item.apply(Stage.DUBBING, E.PUT_BACK)       # a Continue that did not reach the dubbing
            if t.prepared and t.state in (TaskState.PAUSED, TaskState.DONE):
                item.facts["count"], item.facts["detected_language"] = len(t.prepared.cues), t.prepared.code
            if run.state == RunState.AWAITING_TRANSLATION and not any(i.state(Stage.SUBTITLES) == S.PAUSED for i in run.items()):
                run.end_phase()                # the last waiting video was translated or cancelled on its own
            if t.prepared:
                item.facts["transcribed"] = True
                if (t.result or {}).get("replaced"):      # the video changed file
                    item.video = t.result["mkv"].replace("\\", "/")
                    item.title = Path(item.video).name
                observed.observe(run, [item])
            run.save()
        if t.state in (TaskState.DONE, TaskState.ERROR, TaskState.CANCELLED):
            self._follow(run, item.id)
        self.queue.wake.set()
