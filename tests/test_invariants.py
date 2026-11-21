"""What must always hold, whatever the user does and in whatever order: the invariants validated by the author on
21/11/2026, checked after every action of a user drawn at random by Hypothesis (stateful testing).

Hypothesis draws the settings of a project (order of the work, translator free or paid, AI review of the names or
none, the model that reviews, pause before a free translation) and which videos have names to check, then a sequence
of actions a user can do on the page (Run, a video run from its line, Pause, Stop, a go-ahead, the names decided, a box
ticked or not, a video cancelled, the work going on). Only what the page offers is done (a button shown, a panel at
sight), and each one must be accepted by the service. After each action, every invariant is checked; when one breaks,
Hypothesis shrinks the sequence to the shortest one that breaks it.

The real service is at work (Manager, TaskQueue, the rules, the project files), driven step by step: `work` runs the
next task of the queue. Only its edge is replaced: the transcription is kept on the disk, the model of the review
answers what the test drew, the translation is recorded instead of being made.

The invariants checked here (their numbers are those of the list validated):
 1. No paid model runs without a go-ahead given after its cost was shown.
 3, 21. A video is translated by its own translator if it has one, otherwise by the project's.
 6. Once what a video waited for is settled, it goes on without another click when what follows is free.
 7. A video does not stop twice for the same reason.
 8. A video that waits always has something on the page to unblock it.
 11. "Resume" only on a project run then paused or stopped; otherwise "Run".
 12, 14. A button or a panel shown is accepted by the service.
 13. The state shown tells the truth.
 15. Nothing is transcribed twice.
 19, 25. The project read again from its file has the same steps, ticks and settings.
 22. A video unticked is never taken by Run.
 23, 24. A project "in progress" always moves on: never stuck with nothing at work and nothing to answer.
 29. The counts are right.
 Q1. A go-ahead given for videos run on their own gives the model to them, not to the project.
The other invariants of the list are checked by the tests of their own module (the money of a review, the files of
the user, the fixes applied, the languages locked, the texts of the page)."""
import json
import os
import re
import shutil
import tempfile
from pathlib import Path

import pytest
from hypothesis import HealthCheck, settings, strategies as st
from hypothesis.stateful import RuleBasedStateMachine, initialize, invariant, precondition, rule

from subtitles import config, estimation, pipeline, review, translation
from subtitles.project import Stage, StageState
from subtitles.states import RunState, TaskState
from subtitles.web.manager import Manager
from subtitles.web.runs import ProjectRun
from subtitles.web.tasks import TaskQueue

S = StageState
LOCAL = {"id": "local", "type": "openai", "name": "Local", "url": "http://127.0.0.1:8080", "model": "qwen"}
CLAUDE = {"id": "claude", "type": "claude", "name": "Claude", "model": "claude-sonnet-5-5"}
NAMES = ["Gariaev", "Kheiron", "Baptistine"]                     # one per video: a name is submitted once per project
_REQUEST = re.compile(r"^(\d+)\. (.+?) \(\d+ fois\)", re.M)

# Longer runs: INVARIANTS_EXAMPLES=2000 python -m pytest tests/test_invariants.py
EXAMPLES = int(os.environ.get("INVARIANTS_EXAMPLES") or 150)


def paid(engine: dict | None) -> bool:
    return bool(engine) and estimation.pause_by_default(engine)


class Model:
    """The model of every translator: the review of the names proposes the name of a video the test drew; recorded."""

    def __init__(self, user: "User", engine: dict):
        self.user, self.engine = user, engine

    def prepare(self):
        pass

    def finish(self):
        pass

    def complete(self, system, request, schema=None, effort=None):
        title = request.split("\n", 1)[0].removeprefix("Titre : ").strip()
        self.user.spent("review", self.engine, title)
        entries = [{"index": int(n), "status": "to_fix", "spelling": name + "e", "french": "", "confidence": "high",
                    "reason": "test"} for n, name in _REQUEST.findall(request) if name in self.user.with_names]
        return json.dumps({"entries": entries})


class User(RuleBasedStateMachine):
    """A user of the page, and what the service must do whatever they do."""

    def __init__(self):
        super().__init__()
        self.tmp = Path(tempfile.mkdtemp(prefix="invariants-"))
        self.mp = pytest.MonkeyPatch()
        self.broken: list[str] = []                # invariants broken inside the service (a paid model without a go-ahead…)
        self.launched = False                      # Run pressed once on the project
        self.paused_by_user = False                # Pause or Stop pressed since
        self.alone: set[str] = set()               # videos run from their line while the project did not run
        self.agreed: set[str] = set()              # videos a go-ahead was given for
        self.transcribed: dict[str, int] = {}
        self.translated: list[tuple[str, str, str]] = []     # (video, translator used, translator expected)
        self.pauses: dict[str, list[str]] = {}               # task → the reasons of its pauses, in order

    def teardown(self):
        try:
            self.manager.close()
        except AttributeError:
            pass
        self.mp.undo()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # --- the edge of the service -----------------------------------------------------------------------------------

    def spent(self, what: str, engine: dict, title: str):
        item = next((i for i in self.run.project.items if Path(i.video).stem == title or Path(i.video).name == title), None)
        if paid(engine) and not self.run.options.get("spend_agreed") and (item is None or item.id not in self.agreed):
            self.broken.append(f"1: {what} by the paid model {engine['id']} for {title} without a go-ahead")

    def translate_and_copy(self, r, p, log, step, cancelled):
        item = next(i for i in self.run.project.items if Path(i.video) == Path(p.video))
        engine = config.engine(self.cfg, "translators", "active_translator", r.translator_id)
        self.spent("translation", engine, Path(p.video).stem)
        expected = item.overrides.get("translator") or self.run.options.get("translator") or self.cfg["active_translator"]
        self.translated.append((item.id, engine["id"], expected))
        return pipeline.Result(p.video, p.source_srt, p.source_srt, p.code, len(p.cues), True)

    # --- the project the test starts from --------------------------------------------------------------------------

    @initialize(order=st.sampled_from(["steps", "videos"]), translator=st.sampled_from(["local", "claude"]),
                review_mode=st.sampled_from(["", "names"]), reviewer=st.sampled_from(["", "local"]),
                pause=st.booleans(), videos=st.integers(1, 3), with_names=st.sets(st.sampled_from(NAMES)))
    def a_project(self, order, translator, review_mode, reviewer, pause, videos, with_names):
        self.with_names = with_names
        self.cfg = {"output_dir": str(self.tmp / "out"), "transcriptions": [{"id": "t", "url": "http://127.0.0.1:9"}],
                    "active_transcription": "t", "translators": [LOCAL, CLAUDE], "active_translator": "local",
                    "target_languages": ["fr"], "cleanup": {}}
        self.mp.setattr(config, "load", lambda *a, **k: self.cfg)
        self.mp.setattr(translation, "create", lambda m, log=print: Model(self, m))
        self.mp.setattr(pipeline, "translate_and_copy", self.translate_and_copy)
        files = []
        for n in range(videos):
            video = self.tmp / f"Episode {n + 1}.mp4"
            video.write_bytes(b"x")
            work = pipeline.work_dir(self.tmp / "out", video)
            work.mkdir(parents=True)
            said = [f"We met {NAMES[n]} in the laboratory.", "He works on the genome.", f"Thank you {NAMES[n]} for watching!"]
            words, at = [], 0.0
            for s in said:
                for w in s.split():
                    words.append({"word": w, "start": round(at, 2), "end": round(at + 0.4, 2)})
                    at += 0.5
                at += 1.0
            (work / "transcription.json").write_text(json.dumps({"words": words, "segments": [], "language": "en", "text": " ".join(said)}),
                                                     encoding="utf-8")
            files.append(video)
        real_prepare = pipeline.prepare

        def prepare(r, *a, **k):                   # the transcription is on the disk: a second one would be a redo
            name = Path(r.source).stem
            if r.redo:
                self.transcribed[name] = self.transcribed.get(name, 0) + 1
            return real_prepare(r, *a, **k)
        self.mp.setattr(pipeline, "prepare", prepare)
        self.queue = TaskQueue(history=[])
        self.manager = Manager(self.queue, start=False)
        options = {"translator": translator, "languages": ["fr"], "review": review_mode or None, "review_engine": reviewer or None,
                   "order": "videos" if order == "videos" else None, "pause": True if pause else None}
        self.run = self.manager.projects.create(self.tmp.as_posix(), [{"path": v.as_posix(), "title": v.name} for v in files],
                                                {k: v for k, v in options.items() if v is not None}, "Mine", [Stage.SUBTITLES], start=False)
        self.ids = [i.id for i in self.run.project.items]

    # --- what the page shows ---------------------------------------------------------------------------------------

    def view(self) -> dict:
        return self.run.summary()

    def item_view(self, item_id: str) -> dict:
        return next(i for i in self.view()["items"] if i["id"] == item_id)

    def offered(self, item_id: str) -> list[str]:
        return self.item_view(item_id)["stages"][Stage.SUBTITLES]["actions"]

    def proposals(self) -> list[dict]:
        return self.manager.projects.proposals(self.run.id)["proposals"]

    def queued(self) -> list:
        return [t for t in self.queue.tasks.values() if t.state == TaskState.QUEUED]

    def feed(self):
        self.manager.projects.feed()

    # --- what the user does ----------------------------------------------------------------------------------------

    @rule()
    def run_button(self):
        r = self.view()["rules"]
        if not (r["run"]["open"] or r["run"]["resume"]) or self.run.state in (RunState.RUNNING, RunState.CANCELLED):
            return                                 # the button waits
        assert self.manager.projects.start(self.run.id), "12: Run shown and refused"
        self.launched, self.paused_by_user = True, False
        self.feed()

    @rule(n=st.integers(0, 2))
    def run_one_video(self, n):
        item_id = self.ids[n % len(self.ids)]
        actions = self.offered(item_id)
        button = next((a for a in ("run", "translate_left", "retry", "translate") if a in actions), None)
        if button is None:
            return
        not_running = self.run.state != RunState.RUNNING
        assert self.manager.projects.run_item(self.run.id, item_id, Stage.SUBTITLES), f"14: {button} shown on its line and refused"
        if button == "translate":
            self.agreed.add(item_id)
        if not_running:
            self.alone.add(item_id)
        self.feed()

    @rule(model=st.sampled_from(["local", "claude"]), no_more=st.booleans())
    def go_ahead(self, model, no_more):
        if not self.view()["rules"]["go_ahead"]:
            return
        waiting = [i for i in self.run.project.selected() if i.state(Stage.SUBTITLES) == S.PAUSED]
        only_alone = bool(waiting) and all(i.id in self.alone for i in waiting) and self.run.state != RunState.AWAITING_TRANSLATION
        before = self.run.options.get("translator")
        choice = {"spend_agreed": True} if no_more else None
        assert self.manager.projects.translate(self.run.id, model, None, review_choice=choice), "12: the go-ahead shown and refused"
        self.agreed.update(i.id for i in waiting)
        if only_alone:
            assert self.run.options.get("translator") == before, \
                "Q1: a go-ahead for videos run on their own changed the translator of the project"
        self.feed()

    @rule(accept=st.booleans())
    def decide_names(self, accept):
        names = self.proposals()
        if not names:
            return
        self.manager.projects.validate_names(self.run.id, [{**p, "accept": accept} for p in names])
        self.feed()

    @rule()
    def pause(self):
        if self.manager.projects.suspend(self.run.id) and self.launched:
            self.paused_by_user = True

    @rule()
    def stop(self):
        at_work = {i.id for i in self.run.project.items if i.state(Stage.SUBTITLES) == S.RUNNING}
        if self.run.state in (RunState.RUNNING, RunState.SUSPENDED) and self.manager.projects.stop(self.run.id):
            self.agreed -= at_work                 # what was stopped starts again from the start: its costs are asked again
            if self.launched:
                self.paused_by_user = True
        self.feed()

    @rule(n=st.integers(0, 2), on=st.booleans())
    def tick(self, n, on):
        item = self.run.project.items[n % len(self.ids)]
        if item.state(Stage.SUBTITLES) in (S.RUNNING, S.PAUSED):
            return
        self.manager.projects.select(self.run.id, {item.id: on}, Stage.SUBTITLES)

    @rule(n=st.integers(0, 2))
    def cancel_video(self, n):
        item_id = self.ids[n % len(self.ids)]
        if "cancel" in self.offered(item_id):
            assert self.manager.projects.cancel_item(self.run.id, item_id, Stage.SUBTITLES), "14: Cancel shown and refused"
            self.agreed.discard(item_id)           # cancelled: taken again, it asks again
            self.feed()

    @rule()
    def restart(self):
        """The service stops and starts again (a reboot, or its own stop after a while): the project is read from its file."""
        self.manager.close()
        self.queue = TaskQueue(history=[])
        self.manager = Manager(self.queue, start=False)
        self.run = self.manager.projects.load(str(self.run.file))
        assert not self.run.options.get("spend_agreed"), "1: do not ask again outlived the session"
        self.agreed.clear()                        # the go-ahead given before is not known any more: asked again
        self.alone.clear()
        self.paused_by_user = self.launched        # interrupted: what was run is resumed
        self.feed()

    @precondition(lambda self: bool(self.queued()))
    @rule()
    def work(self):
        """The next task of the queue does its pass (up to its end or to a pause)."""
        t = self.queued()[0]
        selected = {i.id for i in self.run.project.selected()}
        item = self.run.item_of_task(t.id)
        assert item is None or item.id in selected or item.id in self.alone or item.ticked(Stage.SUBTITLES), \
            "22: a video unticked taken by Run"
        self.queue._run(t)
        if t.state == TaskState.PAUSED:
            self.pauses.setdefault(t.id, []).append(t.waits)
        self.feed()

    # --- the invariants --------------------------------------------------------------------------------------------

    @invariant()
    def nothing_broken_inside(self):
        assert not self.broken, self.broken[0]

    @invariant()
    def resume_only_after_a_pause(self):
        if not hasattr(self, "run"):
            return
        if self.view()["rules"]["run"]["resume"]:
            assert self.launched and self.paused_by_user, "11: the button says Resume on a project never paused nor stopped"

    @invariant()
    def a_video_that_waits_can_be_unblocked(self):
        if not hasattr(self, "run"):
            return
        v = self.view()
        mine = {p["transcribed"] for p in self.proposals()}
        for item in self.run.project.selected():
            if item.state(Stage.SUBTITLES) == S.PAUSED:
                assert v["rules"]["go_ahead"] or mine or "translate" in self.offered(item.id), \
                    "8: a video waits and nothing on the page unblocks it"

    @invariant()
    def the_state_tells_the_truth(self):
        if not hasattr(self, "run"):
            return
        v = self.view()
        status = v["rules"]["status"]
        assert (status == "awaiting_translation") == v["rules"]["go_ahead"], f"13: {status} and go_ahead {v['rules']['go_ahead']}"
        if v["rules"]["go_ahead"]:
            assert any(i.state(Stage.SUBTITLES) == S.PAUSED for i in self.run.project.selected()), \
                "13: the go-ahead is asked and no video waits for it"
        ticked = [i for i in self.run.project.selected() if i.ticked(Stage.SUBTITLES)]
        if status == "done":                       # cancelled or failed: ended too (Run offers to take them again)
            assert not any(i.state(Stage.SUBTITLES) in (S.TODO, S.RUNNING, S.PAUSED) for i in ticked), "13: done with a ticked video left"
        finished = sum(1 for i in self.run.project.selected() if all(not i.ticked(s) or i.state(s) in (S.DONE, S.SKIPPED)
                                                                     for s in self.run.project.stages))
        assert v["rules"]["finished"] == finished, f"29: {v['rules']['finished']} finished shown, {finished} really"

    @invariant()
    def each_video_by_its_translator(self):
        for item_id, used, expected in getattr(self, "translated", []):
            assert used == expected, f"3: video {item_id} translated by {used}, its translator is {expected}"

    @invariant()
    def nothing_transcribed_twice(self):
        for name, n in getattr(self, "transcribed", {}).items():
            assert n <= 1, f"15: {name} transcribed {n} times"

    @invariant()
    def a_video_does_not_stop_twice_for_the_same_reason(self):
        for task, reasons in getattr(self, "pauses", {}).items():
            assert len(reasons) == len(set(reasons)), f"7: task {task} stopped for {reasons}"

    @invariant()
    def the_file_says_the_same(self):
        if not hasattr(self, "run") or not self.run.file.is_file():
            return
        again = ProjectRun.from_file(self.run.file)
        for a, b in zip(self.run.project.items, again.project.items):
            assert a.ticked(Stage.SUBTITLES) == b.ticked(Stage.SUBTITLES), "19: a tick lost in the file"
            if a.state(Stage.SUBTITLES) in (S.DONE, S.TODO):
                assert b.state(Stage.SUBTITLES) == a.state(Stage.SUBTITLES), \
                    f"19: {a.state(Stage.SUBTITLES)} read back as {b.state(Stage.SUBTITLES)}"
        mine = {k: v for k, v in self.run.options.items() if k != "spend_agreed"}            # for the session only
        assert again.options == mine, "19: the settings of the project read back otherwise"

    @invariant()
    def nothing_waits_for_nothing(self):
        """When nothing is left in the queue: a project in progress has something to answer, and a video that waits
        waits for a reason still there (6, 23, 24)."""
        if not hasattr(self, "run") or self.queued():
            return
        v = self.view()
        mine = {p["transcribed"] for p in self.proposals()}
        for item in self.run.project.selected():
            if item.state(Stage.SUBTITLES) != S.PAUSED:
                continue
            t = self.queue.get(item.stages[Stage.SUBTITLES].task or "")
            if t is None or t.prepared is None:
                continue
            names = {c["name"] for c in review.candidates(" ".join(c.text for c in t.prepared.cues))}
            engine = config.engine(self.cfg, "translators", "active_translator",
                                   item.overrides.get("translator") or self.run.options.get("translator"))
            reasons = {"names": bool(names & mine), "cost": paid(engine) and item.id not in self.agreed and not self.run.options.get("spend_agreed"),
                       "review cost": t.waits == "review", "pause asked": self.run.options.get("pause") is True and item.id not in self.agreed,
                       "project paused": self.run.state == RunState.SUSPENDED and self.launched}
            assert any(reasons.values()), f"6: {item.video} waits ({t.waits}) for nothing: its names are decided and what follows is free"
        if v["rules"]["status"] == "running":
            at_work = any(i.state(Stage.SUBTITLES) == S.RUNNING for i in self.run.project.items)
            assert at_work or v["rules"]["go_ahead"] or mine, "23: in progress with nothing at work and nothing to answer"


User.TestCase.settings = settings(max_examples=EXAMPLES, stateful_step_count=25, deadline=None,
                                  suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much])
TestUser = User.TestCase
