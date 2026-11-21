"""Projects at work, as the page and the command line see them: creation, resume from the project file, the videos
added, ticked or removed, the settings, and the actions on a project (start, suspend, stop, translate, continue,
goal…). Each command checks that it is possible now, changes the project and wakes the work.

What runs the work is underneath: the scheduler (scheduler.py: the next tasks, one step of one video, the events of
the queue), the downloads and the reading of the sites (fetching.py), the graphics card (card.py), what the disk shows
done (observed.py) and the last project offered at the next start (last_project.py).
"""
import json
import os
import time
from pathlib import Path

from .. import config, corrections, dubbing, estimation, glossary, i18n, language, pipeline, review, source, steps
from ..project import STAGES, Event, Item, Project, Stage, StageState, rules, settings, store
from ..project.choices import (LANGUAGE_KEYS, RECHECK_KEYS, STEP_OF_SETTING, normalized, origin_of, own, stages_for, stop_for,
                               tick_with_needs, until_of)
from ..states import ACTIVE_TASK_STATES, FINISHED_RUN_STATES, RunState, TaskState, VideoState
from . import last_project, observed
from .runs import ProjectRun
from .scheduler import Scheduler

S = StageState
E = Event


class Orchestrator(Scheduler):
    # --- creation and resume ---
    def create(self, root: str, videos: list[dict], options: dict, title: str = "", stages: list[Stage] | None = None,
               start: bool = True) -> ProjectRun:
        """New project: `videos` [{path or source, language?, title?}], a video file or an address each. `stages`: the
        steps ticked (by default from `stop`, the download if there are addresses); `start`: False leaves it
        suspended, to be started later (the page of the four steps)."""
        cfg = config.load()
        dest = {**pipeline.DEFAULT_DESTINATION, **(cfg.get("destination") or {}), **(options.get("destination") or {})}
        file = store.path_for(Path(root), Path(options.get("output_dir") or cfg["output_dir"]), bool(dest.get("output_dir")))
        items = [self._new_item(v) for v in videos]
        options = dict(options)
        if stages is None:
            stages = stages_for(options.get("stop"), any(i.is_address for i in items))
        else:
            options["stop"] = stop_for(stages, options.get("stop"), options.get("mkv"))
        project = Project(root, items, options, stages, title)
        run = ProjectRun(project, file)
        run.follow_ticks()
        if not start:
            run.state = RunState.SUSPENDED
        return self._open(run)

    @staticmethod
    def _new_item(v: dict) -> Item:
        return Item(v.get("source") or v["path"], title=v.get("title") or "", selected=v.get("selected", True) is not False,
                    overrides={"language": v["language"]} if v.get("language") else {},
                    facts={"duration": v["duration"]} if v.get("duration") else {})

    def load(self, file: str) -> ProjectRun:
        run = ProjectRun.from_file(Path(file))
        for other in list(self.all.values()):                  # a single run per project
            if (other.file == run.file or other.id == run.id) and other.state not in FINISHED_RUN_STATES:
                self.cancel(other.id)                          # outside the lock: cancelling a task archives it, which takes it
        return self._open(run)

    def _open(self, run: ProjectRun) -> ProjectRun:
        run.after_save, run.step_of, run.progress_of = last_project.remember, self._task_step, self.fetching.progress
        run.work_of = self._task_work
        with self.lock:
            self.all[run.id] = run
        for item in run.project.items:                         # a video already downloaded is not downloaded again
            if item.is_address and item.state(Stage.DOWNLOAD) == S.TODO and item.video and Path(item.video).is_file():
                item.apply(Stage.DOWNLOAD, E.FOUND_DONE)
        observed.mark_transcribed(run)
        run.save()
        observed.measure_durations(run)
        self.queue.wake.set()
        return run

    # --- the videos of a project ---
    def already_there(self, ident: str, videos: list[dict]) -> list[dict]:
        """Among `videos`, those the project already holds: [{source, title, kind}] with the title of the line that
        holds each: an address of the list or the file it downloaded ("address"), a file ("file"), or the address a
        file of the project comes from ("origin": downloading it again would make a second copy)."""
        run = self.all.get(ident)
        if not run:
            return []
        lines = {}
        for i in run.project.items:
            kind = "address" if i.is_address else "file"
            for key, how in ((i.source, kind), (i.video, kind), ("" if i.is_address else origin_of(i), "origin")):
                if key:
                    lines.setdefault(key, (i, how))
        found = []
        for v in videos:
            given = v.get("source") or v.get("path")
            if given in lines:
                i, how = lines[given]
                found.append({"source": given, "title": i.title or Path(i.video or i.source).name, "kind": how})
        return found

    def add_items(self, ident: str, videos: list[dict]) -> int:
        """Adds videos to a project (back to Select): those already in it are skipped. Returns how many were added."""
        run = self.all.get(ident)
        if not run or run.state == RunState.CANCELLED:
            return 0
        known = {i.source for i in run.project.items} | {i.video for i in run.project.items} \
            | {origin_of(i) for i in run.project.items if not i.is_address}
        new = [self._new_item(v) for v in videos if (v.get("source") or v.get("path")) not in known]
        with run.lock:
            # the new videos are ticked for the steps of the project (download and subtitles if none is ticked any
            # more); an address is downloaded if it is to be subtitled
            wanted = set(run.project.stages) or {Stage.DOWNLOAD, Stage.SUBTITLES}
            if Stage.SUBTITLES in wanted:
                wanted.add(Stage.DOWNLOAD)
            for item in new:
                for s in STAGES:
                    if s not in wanted and item.state(s) == S.TODO:
                        item.stages[s].selected = False
            run.project.items += new
            run.follow_ticks()
            observed.mark_transcribed(run)
            run.save()
        observed.measure_durations(run, new)
        return len(new)

    def select(self, ident: str, choices: dict[str, bool], stage: Stage | None = None) -> bool:
        """Ticks or unticks items ({item id: ticked}) for one step (its screen), or for every step (the Select screen)."""
        run = self.all.get(ident)
        if not run:
            return False
        with run.lock:
            for item in run.project.items:
                if item.id in choices:
                    tick_with_needs(item, stage, bool(choices[item.id]))
            run.follow_ticks()
            run.save()
        self.queue.wake.set()
        self.fetching.poke.set()
        return True

    def remove_items(self, ident: str, ids: list[str]) -> int:
        """Takes items out of the project, except those being worked on. Returns how many were removed."""
        run = self.all.get(ident)
        if not run:
            return 0
        with run.lock:
            keep = [i for i in run.project.items if i.id not in ids or rules.busy(i)]
            removed = len(run.project.items) - len(keep)
            for i in run.project.items:
                if i not in keep:
                    self.fetching.drop_partial(run, i)
            run.project.items = keep
            run.save()
        return removed

    def relocate(self, ident: str, title: str | None = None, base: str | None = None, named: bool | None = None) -> str:
        """Renames the project, and moves it to `base` (plus a subfolder named after it if `named`) as long as nothing
        has started: its file goes there, and the folder left is removed if empty. Once started, only its name changes
        (its videos and subtitles are already in its folder). Returns "" or the key of the reason it was refused."""
        run = self.all.get(ident)
        if not run:
            return "err.project_unknown"
        with run.lock:
            title = (title or "").strip() or run.project.title
            old_root = Path(run.root)
            new_root = old_root
            if base is not None and str(base).strip():
                b = Path(os.path.expanduser(str(base).strip()))
                if not b.is_absolute():
                    return "err.folder_not_absolute"
                new_root = b / source.folder_name(title) if named else b
            if os.path.normcase(os.path.abspath(new_root)) != os.path.normcase(os.path.abspath(old_root)):
                if run.started() or any(i.id in self.fetching.downloads for i in run.project.items):
                    return "err.project_started_move"
                in_root = os.path.normcase(os.path.abspath(run.file.parent)) == os.path.normcase(os.path.abspath(old_root))
                new_file = new_root / store.FILE_NAME if in_root else run.file
                if in_root and new_file.exists():
                    return "err.project_already_there"
                if new_root.exists() and not new_root.is_dir():
                    return "err.not_a_folder"
                new_root.mkdir(parents=True, exist_ok=True)
                old_file = run.file
                run.project.folder, run.project.title, run.file = new_root.as_posix(), title, new_file
                run.save()
                if old_file != new_file:
                    old_file.unlink(missing_ok=True)
                    try:
                        old_root.rmdir()           # only if empty: the videos of the user stay where they are
                    except OSError:
                        pass
            else:
                run.project.title = title
                run.save()
        return ""

    # --- settings chosen for one video, or for the project ---
    def set_item_settings(self, ident: str, item_id: str, changes: dict) -> bool:
        """{key: value} for this video only (None: back to the project's); unknown keys are refused."""
        run = self.all.get(ident)
        item = run.project.item(item_id) if run else None
        if item is None or not set(changes) <= settings.ITEM_KEYS:
            return False
        if LANGUAGE_KEYS & set(changes) and rules.locked([item]):
            return False
        own_review = changes.get("review")      # for one video, "none" says no review whatever the project asks
        changes = normalized({k: v for k, v in changes.items() if k != "review"})
        if own_review is not None or "review" in changes:
            changes["review"] = own_review if own_review in ("none", "names", "full") else None
        with run.lock:
            for k, v in changes.items():
                settings.override(item, k, v)
            if RECHECK_KEYS & set(changes):
                observed.observe(run, [item], recheck=True)          # a language added: its translation to do
            for k, v in changes.items():
                stage = STEP_OF_SETTING.get(k)          # a choice of its own for a step: the video wants that step
                if v is not None and stage and item.state(stage) not in (S.SKIPPED, S.DONE, S.RUNNING, S.PAUSED):
                    tick_with_needs(item, stage, True)
            run.follow_ticks()
            run.save()
        return True

    def set_project_settings(self, ident: str, changes: dict) -> bool:
        run = self.all.get(ident)
        if run is None or not set(changes) <= settings.PROJECT_KEYS:
            return False
        if LANGUAGE_KEYS & set(changes) and rules.locked(run.project.items):
            return False
        changes = normalized(changes)
        with run.lock:
            for k, v in changes.items():
                if v is None:
                    run.options.pop(k, None)
                else:
                    run.options[k] = v
            if "mkv" in changes and steps.goes_to(run.options.get("stop"), "dubbing"):
                run.options["stop"] = stop_for([Stage.DUBBING], run.options.get("stop"), run.options.get("mkv"))
            if RECHECK_KEYS & set(changes):
                observed.observe(run, recheck=True)
            run.save()
        return True

    # --- queries ---
    def get(self, ident: str) -> ProjectRun | None:
        return self.all.get(ident)

    def active(self) -> ProjectRun | None:
        return next((r for r in reversed(list(self.all.values())) if r.state not in FINISHED_RUN_STATES), None)

    def running(self) -> ProjectRun | None:
        return next((r for r in list(self.all.values()) if r.state == RunState.RUNNING), None)

    def summaries(self) -> list[dict]:
        return [r.summary() for r in reversed(list(self.all.values()))]

    def proposals(self, ident: str, path: str | None = None) -> dict | None:
        """Names to check: those of a video, or all those of the project."""
        run = self.all.get(ident)
        if not run:
            return None
        names = None
        if path:
            f = pipeline.work_dir(run.options.get("output_dir") or config.load()["output_dir"], path) / review.VIDEO_FILE
            try:
                names = set(json.loads(f.read_text(encoding="utf-8")).get("names", []))
            except (OSError, ValueError):
                names = set()
        return {"proposals": review.proposals(run.file, names), "path": path or "", "corrections": self._pending_corrections(run, path),
                "applied": self._applied_alone(run, path)}

    @staticmethod
    def _work(run: ProjectRun, item: Item) -> Path:
        return pipeline.work_dir(run.options.get("output_dir") or config.load()["output_dir"], item.video)

    def _pending_corrections(self, run: ProjectRun, path: str | None = None) -> list[dict]:
        """The lines the full AI review proposes to fix, video by video (those of one video with `path`), in the order
        of the list: [{item, title, corrections: [{id, line, start, original, corrected, reason, confidence}]}]."""
        return self._corrections(run, path, corrections.pending)

    def _corrections(self, run: ProjectRun, path: str | None, of) -> list[dict]:
        out = []
        wanted = path.replace("\\", "/") if path else None
        for item in run.project.items:
            if not item.video or (wanted and wanted not in (item.video, item.id)):
                continue
            mine = of(self._work(run, item))
            if mine:
                out.append({"item": item.id, "title": item.title or Path(item.video).name,
                            "corrections": [{k: c.get(k) for k in ("id", "line", "start", "original", "corrected", "reason", "confidence")} for c in mine]})
        return out

    def _applied_alone(self, run: ProjectRun, path: str | None = None) -> dict:
        """What was applied without a question in a project that asked for it (`review_auto`), to be shown and taken
        back: {names: [{transcribed, spelling, french, reason}], corrections: as _pending_corrections}."""
        return {"names": [{k: e.get(k) or "" for k in ("transcribed", "spelling", "french", "reason")} for e in review.applied_alone(run.file)],
                "corrections": self._corrections(run, path, corrections.applied_alone)}

    def subtitles_of(self, ident: str, item_id: str, wanted: str = "") -> dict | None:
        """The subtitles of a video as they are now, numbered like the AI review numbers them: {transcribed, item, title,
        spoken_language, language, count, waiting, lines: [{line, start, end, text}]}; with `wanted`, another language
        than the spoken one, each line also has its `translation` (`translated`: false without one of this
        segmentation; `stale`: the lines whose original changed since). None: unknown project or video."""
        run = self.all.get(ident)
        item = run.project.item(item_id) if run else None
        if item is None:
            return None
        cues = pipeline.kept_cues(config.load(), self._work(run, item), glossary.path_for(run.file)) if item.video else None
        if cues is None:
            return {"transcribed": False}
        work = self._work(run, item)
        spoken = item.facts.get("detected_language") or item.overrides.get("language") or ""
        out = {"transcribed": True, "item": item.id, "title": item.title or Path(item.video).name, "spoken_language": spoken,
               "language": spoken, "count": len(cues), "waiting": len(corrections.pending(work)),
               "lines": [{"line": i + 1, "start": round(c.start, 2), "end": round(c.end, 2), "text": c.text} for i, c in enumerate(cues)]}
        wanted = (wanted or "").strip().lower()
        if wanted and wanted != spoken:
            before = pipeline.kept_cues(config.load(), work, glossary.path_for(run.file), fixes=False) if corrections.accepted(work) else None
            translated, changed = pipeline.translation_of(work, wanted, [c.text for c in cues], [c.text for c in before] if before else None)
            out.update(language=wanted, translated=translated is not None, stale=[i + 1 for i in changed])
            for line, text in zip(out["lines"], translated or []):
                line["translation"] = text
        return out

    def propose_corrections(self, ident: str, item_id: str, found: list[dict], by: str = "", complete: bool = True) -> dict | None:
        """Lines to fix proposed from outside (an assistant, through the MCP server) for a video already transcribed:
        kept under the rules of the AI review, they wait for a decision in the page like those of a model. →
        {transcribed, busy, proposed, refused: [{line, why}], waiting, state}; None: unknown project or video."""
        run = self.all.get(ident)
        item = run.project.item(item_id) if run else None
        if item is None:
            return None
        if not item.video:
            return {"transcribed": False}
        if item.state(Stage.SUBTITLES) == S.RUNNING:           # its subtitles are being made: the lines would move under the proposals
            return {"transcribed": True, "busy": True}
        work = self._work(run, item)
        cues = pipeline.kept_cues(config.load(), work, glossary.path_for(run.file))
        if cues is None:
            return {"transcribed": False}
        with run.lock:
            kept, refused = corrections.propose(work, cues, found, by or "assistant", complete)
            task = self.queue.get(item.stages[Stage.SUBTITLES].task or "") if item.state(Stage.SUBTITLES) == S.PAUSED else None
            if task is not None and kept:
                task.asked_validation = False                  # it waits for a go-ahead: these lines are to decide first
            observed.observe(run)
            run.review_stamp += 1
            run.save()
        return {"transcribed": True, "busy": False, "proposed": len(kept), "refused": refused, "waiting": len(corrections.pending(work)),
                "state": item.state(Stage.SUBTITLES).value}

    def validate_names(self, ident: str, decisions: list[dict], lines: list[dict] | None = None, undo: dict | None = None) -> dict | None:
        """The decisions on the proposals of the AI review: `decisions` for the names ({transcribed, spelling, french,
        accept}, see review.validate), `lines` for the lines of the full review ({item, id, accept, corrected?}).
        `undo` {names?: [transcribed], lines?: [{item, id}]}: fixes applied without a question (`review_auto`) taken
        back; the subtitles of their videos are cut without them the next time they are made."""
        run = self.all.get(ident)
        if not run:
            return None
        accepted, rejected = review.validate(run.file, decisions)
        fixed, left = 0, 0

        def by_item(entries) -> dict[str, list[dict]]:
            found: dict[str, list[dict]] = {}
            for x in entries or []:
                if isinstance(x, dict):
                    found.setdefault(str(x.get("item") or ""), []).append(x)
            return {k: v for k, v in found.items() if (item := run.project.item(k)) is not None and item.video}
        for item_id, chosen in by_item(lines).items():
            yes, no = corrections.decide(self._work(run, run.project.item(item_id)), chosen)
            fixed, left = fixed + yes, left + no
        undo = undo if isinstance(undo, dict) else {}
        back = review.take_back(run.file, [str(n) for n in undo.get("names") or []])
        for item_id, chosen in by_item(undo.get("lines")).items():
            back += corrections.take_back(self._work(run, run.project.item(item_id)), [str(x.get("id")) for x in chosen])
        with run.lock:
            observed.observe(run)
            if back:
                run.review_stamp += 1
            self._decided(run)
            run.save()
        self.queue.wake.set()
        return {"accepted": accepted, "rejected": rejected, "lines_accepted": fixed, "lines_rejected": left, "taken_back": back}

    def _decided(self, run: ProjectRun):
        """The videos that waited for the proposals of their review, now all decided, go on without another click: a
        cost or the pause asked before a free translation stops them again (their costs were never accepted), what is
        free does not. Not in a project the user paused: it goes on when they resume it."""
        if run.state == RunState.SUSPENDED and run.project.launched:
            return
        for item in rules.waiting(run.project):
            t = self.queue.get(item.stages[Stage.SUBTITLES].task or "")
            if t is None or t.state != TaskState.PAUSED or t.waits != "validation" or not item.facts.get("validated"):
                continue
            if self.queue.resume(t.id, own(run.options, item, "translator"), self._languages(run, item), self._review_choice(run, item),
                                 agreed=False):
                run.held.discard(item.id)
                item.apply(Stage.SUBTITLES, E.START, task=t.id)
        run.settle()

    def estimate(self, ident: str, languages: list[str] | None = None) -> dict | None:
        """Sum of the estimates of the prepared videos (transcribed, task paused before the translation), with what
        they wait for (`waits`: how many for the cost of their AI review, for a decision on its proposals, for the cost
        of their translation, because their language is doubtful) and the estimate of the AI reviews not run yet
        (`review`, see estimation.review). `doubts`: the videos whose transcription looks little like its language
        ([{item, title, task, code, share, words, opinion}], see language.doubt), and with them `opinion_models`: what
        asking each model for their language takes, and `lists_folder`: where the user adds lists of function words."""
        run = self.all.get(ident)
        if not run:
            return None
        total, waits, reviews, doubts = None, {"language": 0, "review": 0, "validation": 0, "translation": 0}, None, []
        for item in run.items():
            task = item.stages[Stage.SUBTITLES].task
            if item.state(Stage.SUBTITLES) != S.PAUSED or not task:
                continue
            e = self.queue.estimate(task, languages if languages is not None else run.options.get("languages"))
            if not e:
                continue
            if e.get("waits") in waits:
                waits[e["waits"]] += 1
            doubt = e.pop("doubt", None)
            if doubt:
                doubts.append({"item": item.id, "title": item.title or Path(item.video or item.source).name, "task": task, **doubt})
            reviews = self._add_review(reviews, e.get("review"))
            if not e["languages"]:                       # already translated into these languages: will cost nothing
                continue
            if total is None:
                total = {**e, "videos": 0, "translators": [dict(t) for t in e["translators"]]}
            else:
                total["languages"] += [c for c in e["languages"] if c not in total["languages"]]
                for k in ("count", "requests", "input_tokens", "output_tokens"):
                    total[k] += e[k]
                for a, b in zip(total["translators"], e["translators"]):
                    for k in ("cost_usd", "duration_s"):
                        if a.get(k) is not None and b.get(k) is not None:
                            a[k] = round(a[k] + b[k], 4)
            total["videos"] += 1
        return {**(total or {"languages": [], "count": 0}), "waits": waits, "review": reviews, "doubts": doubts,
                **({"opinion_models": estimation.language_question(config.load()), "lists_folder": str(language.USER_LISTS)} if doubts else {})}

    @staticmethod
    def _add_review(total: dict | None, r: dict | None) -> dict | None:
        """The estimate of the AI review of one more video added to the sum (a price unknown for one is for all)."""
        if not r:
            return total
        if total is None:
            return {**r, "videos": 1, "translators": [dict(t) for t in r["translators"]]}
        total["videos"] += 1
        for k in ("count", "names"):
            total[k] += r[k]
        for a, b in zip(total["translators"], r["translators"]):
            for k in ("cost_usd", "duration_s", "input_tokens", "output_tokens", "requests"):
                if k in a:
                    a[k] = round(a[k] + b[k], 4) if a[k] is not None and b.get(k) is not None else None
        return total

    # --- actions (False: not possible in the current state) ---
    def set_stages(self, ident: str, stages: list[Stage]) -> bool:
        """These steps for every video ticked (what Run runs is what is ticked): each step asked is ticked where it is
        left to do, each other step unticked where it has not started (the API, the command line; the page ticks a
        column instead)."""
        run = self.all.get(ident)
        if not run or run.state == RunState.CANCELLED:
            return False
        with run.lock:
            for item in run.project.selected():
                for s in STAGES:
                    if s not in stages and item.state(s) == S.TODO:
                        item.stages[s].selected = False
                for s in stages:                     # with the steps they need (the download of an address to subtitle)
                    if item.state(s) not in (S.DONE, S.SKIPPED):
                        tick_with_needs(item, s, True)
            if not run.project.items:
                run.project.stages = [s for s in STAGES if s in stages]
            run.follow_ticks()
            run.save()
        return True

    def start(self, ident: str, stages: list[Stage] | None = None, only: bool = False) -> bool:
        """Runs the steps ticked on the diagram (or these `stages`, which become the ticked ones) on the items ticked for
        each step, in a suspended or finished project. `only`: runs `stages` without changing the ticks of the diagram
        ("Run this step", from the screen of a step); in a running project, the videos left to do for those steps
        start now too (see _join)."""
        run = self.all.get(ident)
        if run and run.state == RunState.RUNNING and only and stages:
            return self._join(run, stages)
        if not run or run.state in (RunState.CANCELLED, RunState.RUNNING):
            return False
        if stages is not None and not only:
            self.set_stages(ident, stages)
        with run.lock:
            if stages is None:
                run.follow_ticks()
            run.active = list(stages) if only and stages else None
            if run.active and Stage.DUBBING in run.active:
                run.options["stop"] = stop_for(run.active, run.options.get("stop"), run.options.get("mkv"))
            if run.state == RunState.DONE:
                run.phase, run.finished = 1, None
            run.state = RunState.RUNNING
            run.project.launched = True        # paused or stopped from now on, it is resumed (rules.project_view)
            run.save()
        self.queue.wake.set()
        self.fetching.poke.set()
        return True

    def _join(self, run: ProjectRun, stages: list[Stage]) -> bool:
        """"Run this step" while the project runs: the step joins the running work. The downloads join the steps run
        now (so that no more start at once than allowed); the subtitles and the dubbing of each video ticked and left
        to do start as with its own button. False if nothing was left to do."""
        started = False
        for stage in stages:
            items = [i for i in run.items() if rules.left(i, stage)]
            if stage == Stage.DOWNLOAD:
                items = [i for i in items if i.is_address and i.id not in self.fetching.downloads]
                if not items:
                    continue
                with run.lock:
                    for i in items:
                        i.apply(Stage.DOWNLOAD, E.RESET)
                    if not run.wants(Stage.DOWNLOAD):
                        run.active = [Stage.DOWNLOAD, *(run.active or run.project.stages)]
                    run.save()
                self.fetching.poke.set()
                started = True
            else:
                for i in items:
                    started = self.run_item(run.id, i.id, stage) or started
        return started

    def suspend(self, ident: str) -> bool:
        run = self.all.get(ident)
        if not run or run.state != RunState.RUNNING:
            return False
        run.state = RunState.SUSPENDED                       # the running videos and downloads go on to their end
        run.finishing = {i.id for i in run.project.items if any(i.state(s) == S.RUNNING for s in i.stages)}
        run.save()
        return True

    def stop(self, ident: str) -> bool:
        """Pause now: the running videos are interrupted (the transcription server is told by the closed connection,
        the translation stops between two chunks, a download stops at once and keeps its partial files, from which it
        resumes) and go back to the list; what they finished is kept on the disk. A project already paused after its
        running videos stops them too."""
        run = self.all.get(ident)
        if not run or run.state not in (RunState.RUNNING, RunState.SUSPENDED):
            return False
        run.state = RunState.SUSPENDED
        run.save()
        for item in run.project.items:
            self.fetching.stop(item.id, stopped=True)
            for stage in (Stage.SUBTITLES, Stage.DUBBING):
                t = self.queue.get(item.stages[stage].task or "") if item.state(stage) == S.RUNNING else None
                if t:
                    self.queue.cancel(t.id, stopped=True)      # task_changed puts the video back instead of marking it cancelled
        return True

    def resume(self, ident: str, changes: dict | None = None) -> bool:
        """Starts a suspended project again; `changes` (translator, transcription, languages, pause, spend_agreed, stop) apply to the
        videos not started yet."""
        run = self.all.get(ident)
        if not run or run.state != RunState.SUSPENDED:
            return False
        changes = changes or {}
        for k in ("translator", "transcription"):
            if changes.get(k):
                run.options[k] = changes[k]
        if changes.get("languages") is not None:
            run.options["languages"] = language.normalize_codes(changes["languages"])
        for k, v in normalized({k: changes[k] for k in ("pause", "spend_agreed") if changes.get(k) is not None}).items():
            if v is None:
                run.options.pop(k, None)
            else:
                run.options[k] = v
        if changes.get("stop") in steps.STOPS:
            self._set_stop(run, changes["stop"])
        run.state = RunState.RUNNING
        run.save()
        if changes.get("transcription"):
            self._move_transcriptions(run, changes["transcription"])
        self.queue.wake.set()
        return True

    @staticmethod
    def _set_stop(run: ProjectRun, stop: str):
        run.options["stop"] = stop
        dub = steps.goes_to(stop, "dubbing")
        for item in run.project.selected():                  # how far the work goes: the dubbing ticked or not
            if dub and item.state(Stage.DUBBING) == S.TODO:
                item.stages[Stage.DUBBING].selected = item.ticked(Stage.SUBTITLES)
            elif not dub and item.state(Stage.DUBBING) not in (S.RUNNING, S.PAUSED):
                item.stages[Stage.DUBBING].selected = False
        run.follow_ticks()
        run.active = None

    def _move_transcriptions(self, run: ProjectRun, engine: str):
        """After Pause, the running video goes on to its end: if it is still being transcribed by another engine than
        the one just chosen, it is stopped (back in the list) and starts again on the chosen one."""
        for item in run.project.items:
            t = self.queue.get(item.stages[Stage.SUBTITLES].task or "") if item.state(Stage.SUBTITLES) == S.RUNNING else None
            if t and t.state in ACTIVE_TASK_STATES and t.prepared is None and t.pass_no == 1 \
                    and (t.options.get("transcription") or "") != engine:
                self.queue.cancel(t.id, stopped=True)

    def translate(self, ident: str, translator: str | None, languages: list[str] | None, stop: str | None = None,
                  review_choice: dict | None = None) -> bool:
        """The go-ahead for the videos that wait: their AI review if it waited for its cost, then their translation
        (they stop once more if the review proposes something to decide). `review_choice` {review?, review_engine?}:
        another kind of review ("" for none) or another model for it, for the project from now on. In a project that
        does not run (paused, finished: a video run on its own, from its line, waits), the videos that wait go on, on
        their own too: the project stays as it is, and the translator chosen becomes theirs (a choice of their own),
        not the project's. The project's go-ahead gives its translator to the project, the videos that have their own
        keep it."""
        run = self.all.get(ident)
        at_rest = bool(run) and run.state in (RunState.SUSPENDED, RunState.DONE) and rules.asks(run.context(), run.project)
        if not run or not (run.state == RunState.AWAITING_TRANSLATION or at_rest):
            return False
        chosen = {k: v for k, v in (review_choice or {}).items() if k in ("review", "review_engine", "spend_agreed") and v is not None}
        for k, v in normalized(chosen).items():
            if v is None:
                run.options.pop(k, None)
            else:
                run.options[k] = v
        run.held.clear()
        if at_rest and translator is not None:   # the go-ahead of the videos that wait (run on their own): the model is theirs,
            of_project = run.subtitle_settings()["translator"]       # the project keeps its own for the next ones
            for item in rules.waiting(run.project):
                if (item.overrides.get("translator") or of_project) != translator:
                    settings.override(item, "translator", translator)
            translator = None
        if at_rest or not run.by_video:
            if translator is not None:
                run.options["translator"] = translator
            if languages is not None:
                run.options["languages"] = language.normalize_codes(languages)
            if stop:
                self._set_stop(run, stop)
        if run.by_video or at_rest:            # video by video: the video that waits goes on, the next ones after it
            if not at_rest:
                run.state = RunState.RUNNING
            with run.lock:
                for item in run.items():
                    task = item.stages[Stage.SUBTITLES].task
                    if item.state(Stage.SUBTITLES) != S.PAUSED or not task:
                        continue
                    t = self.queue.resume(task, own(run.options, item, "translator"), self._languages(run, item), self._review_choice(run, item))
                    if t is None:
                        item.apply(Stage.SUBTITLES, E.FAIL, error=i18n.t("err.paused_task_missing"))
                    else:
                        item.apply(Stage.SUBTITLES, E.START, task=t.id)
                run.save()
            self.queue.wake.set()
            return True
        run.phase, run.state = 2, RunState.RUNNING
        run.agreed = {i.id for i in run.items() if i.state(Stage.SUBTITLES) == S.PAUSED}      # they wait for their turn from now on
        run.save()
        self.queue.wake.set()
        return True

    def cancel(self, ident: str) -> bool:
        run = self.all.get(ident)
        if not run or run.state in FINISHED_RUN_STATES:
            return False
        run.state, run.finished = RunState.CANCELLED, time.time()
        for item in run.project.items:
            if not self.fetching.stop(item.id):
                self.fetching.drop_partial(run, item)
            for stage in (Stage.DOWNLOAD, Stage.SUBTITLES, Stage.DUBBING):
                task = item.stages[stage].task
                if task and item.state(stage) in (S.RUNNING, S.PAUSED):
                    self.queue.cancel(task)
                if item.state(stage) in (S.RUNNING, S.PAUSED):
                    item.apply(stage, E.CANCEL)
        run.save()
        return True

    def retry(self, ident: str, path: str | None = None) -> bool:
        """Sets a video in error (or all of them) back to do; a finished project or one awaiting translation starts again for it."""
        run = self.all.get(ident)
        if not run or run.state == RunState.CANCELLED:
            return False
        targets = [i for i in run.items() if S.ERROR in (i.state(s) for s in i.stages) and path in (None, i.video, i.source, i.id)]
        if not targets:
            return False
        for item in targets:
            for stage in item.stages:
                if item.state(stage) == S.ERROR:
                    item.apply(stage, E.RESET)
        if run.state in (RunState.DONE, RunState.AWAITING_TRANSLATION):
            run.state, run.finished = RunState.RUNNING, None
        run.save()
        self.queue.wake.set()
        return True

    def continue_video(self, ident: str, path: str) -> bool:
        """Takes a single video to the end of the chain, before the others. Also works on a suspended or finished project."""
        run = self.all.get(ident)
        if not run or run.state == RunState.CANCELLED:
            return False
        wants_dubbing = steps.goes_to(run.options.get("stop"), "dubbing") and dubbing.available(config.load())
        until = run.options["stop"] if wants_dubbing else "translation"
        with run.lock:
            item = run.project.item_by_video(path) or run.project.item(path)
            if item is None or run.video_state(item) == VideoState.RUNNING or not item.video:
                return False
            sub = item.stages[Stage.SUBTITLES]
            t = self.queue.get(sub.task or "")
            if sub.state == S.PAUSED and t and t.state == TaskState.PAUSED:
                t.options.update(until=until_of(item, until))
                t.continued = True
                self.queue.resume(t.id, own(run.options, item, "translator"), self._languages(run, item), self._review_choice(run, item))
            else:
                t = self._add_task(run, item, until=until, pause=run.options.get("pause"), continued=True, check_names=True)
            stage = Stage.SUBTITLES if sub.state != S.DONE else Stage.DUBBING
            item.apply(stage, E.START, task=t.id)
            if stage == Stage.SUBTITLES and wants_dubbing:
                item.stages[Stage.DUBBING].task = t.id          # the same task goes on to the dubbing
            for s in (Stage.SUBTITLES, Stage.DUBBING):
                item.stages[s].error = ""
            if run.state == RunState.DONE:        # starts again for this video; the page follows it like the rest of the project
                run.state, run.finished, run.phase = RunState.RUNNING, None, 3
            run.save()
        self.queue.wake.set()
        return True

    def goal(self, ident: str, stop: str, replace: bool | None = None) -> bool:
        """Changes what the project must produce. Dubbing asked for afterwards: the videos already subtitled become to
        dub again, but nothing starts by itself on a suspended or finished project ("Resume" or "Continue")."""
        run = self.all.get(ident)
        if not run or run.state == RunState.CANCELLED or stop not in steps.STOPS:
            return False
        dub = steps.goes_to(stop, "dubbing") and dubbing.available(config.load())
        finished_key = "muxed" if stop == "mux" else "dubbed"
        with run.lock:
            self._set_stop(run, stop)
            if replace is not None:
                run.options["replace"] = bool(replace) and dub
            for item in run.project.items:
                if not dub or item.state(Stage.SUBTITLES) != S.DONE or item.state(Stage.DUBBING) == S.RUNNING:
                    continue
                if item.facts.get("translated") and not item.facts.get(finished_key):
                    item.apply(Stage.DUBBING, E.RESET)    # to dub (or dubbed, but the MKV is now wanted too)
                else:                                  # already made, or nothing translated to dub
                    item.apply(Stage.DUBBING, E.FOUND_DONE if item.facts.get(finished_key) else E.DROP)
            if run.state == RunState.DONE and dub and any(run.video_state(i, True) == VideoState.TRANSLATED for i in run.items()):
                run.state, run.finished = RunState.SUSPENDED, None
            run.save()
        self.queue.wake.set()
        return True

    def gpu_freed(self, ident: str) -> bool:
        """The user freed the card ("It is done"): the dubbing goes on, the lines waiting for it start."""
        run = self.all.get(ident)
        if not run or run.state == RunState.CANCELLED:
            return False
        with run.lock:
            waiting, waited = run.card.waiting, run.card.wait
            run.card.released, run.card.waiting, run.card.wait = True, [], False
            if waited and run.state == RunState.SUSPENDED:
                run.state = RunState.RUNNING
            run.save()
        for item_id in waiting:
            self.run_item(ident, item_id, Stage.DUBBING)
        self.queue.wake.set()
        return True
