"""What the disk shows of the videos of a project: the steps already done (a restart, or another tool, never makes
work done twice), and what a file says of itself (its duration, the address it came from).

The only place the scheduling reads the working folders: the orchestrator calls it when a project opens, when a
video ends a step and when a setting changes what "done" means.
"""
import subprocess
import threading
from pathlib import Path

from .. import config, language, media, pipeline, review, source, steps
from ..project import Event, Item, Stage, StageState
from ..project.choices import dub_languages_of
from .runs import ProjectRun

S = StageState
E = Event


def mark_transcribed(run: ProjectRun):
    """Videos whose transcription is already there: `ProjectRun.next_item` takes them before those to transcribe."""
    output_dir = run.options.get("output_dir") or config.load()["output_dir"]
    for item in run.project.items:
        item.facts["transcribed"] = bool(item.video) and not run.options.get("redo") and pipeline.transcription_kept(output_dir, item.video)
    observe(run)


def observe(run: ProjectRun, items: list[Item] | None = None, recheck: bool = False):
    """Steps done per video, read from the disk: dots of the list and choice of "Continue". `recheck` (a setting
    changed: languages, dubbed language, final file): a step done that the disk no longer shows done for the new
    settings is to do again (a language added: its translation)."""
    cfg = config.load()
    output_dir = run.options.get("output_dir") or cfg["output_dir"]
    project_languages = run.options.get("languages") or cfg.get("target_languages", ["fr"])
    pending = review.pending_names(run.file)
    for item in items if items is not None else run.project.items:
        if not item.video:
            continue
        languages = language.normalize_codes([*(item.overrides.get("languages") or project_languages),
                                              *dub_languages_of(run.options, item)])    # its own, if chosen; the dubbed ones
        spoken = item.facts.get("detected_language") or item.overrides.get("language") or ""
        done = steps.observe(pipeline.work_dir(output_dir, item.video), item.video, languages, spoken, pending,
                             item.overrides.get("dub_language") or run.options.get("dub_language") or None)
        done.pop("transcribed")            # computed by mark_transcribed (takes "redo" into account) or at the end of the task
        item.facts.update(done)
        # what the disk shows done is done (a project made again over videos already worked on): only from "to do"
        if item.state(Stage.SUBTITLES) == S.TODO and item.facts.get("transcribed") and done["translated"]:
            item.apply(Stage.SUBTITLES, E.FOUND_DONE)
        elif recheck and item.state(Stage.SUBTITLES) == S.DONE and not (item.facts.get("transcribed") and done["translated"]):
            item.apply(Stage.SUBTITLES, E.RESET)
        mux = item.overrides["mkv"] if item.overrides.get("mkv") is not None else run.options.get("stop") != "dubbing"
        finished = done["muxed"] if mux else done["dubbed"]
        if item.state(Stage.DUBBING) == S.TODO and item.state(Stage.SUBTITLES) == S.DONE and finished:
            item.apply(Stage.DUBBING, E.FOUND_DONE)
        elif recheck and item.state(Stage.DUBBING) == S.DONE and not (item.state(Stage.SUBTITLES) == S.DONE and finished):
            item.apply(Stage.DUBBING, E.RESET)


def measure_durations(run: ProjectRun, items: list[Item] | None = None):
    """Duration of each video, and for a file the address it came from, in the background: the time left takes the
    longer ones into account. The tests replace it (no ffprobe)."""
    def measure():
        for item in items if items is not None else list(run.project.items):
            if item.video and not item.facts.get("duration"):
                try:
                    item.facts["duration"] = source.duration(Path(item.video))
                except (source.SourceError, OSError, subprocess.SubprocessError):
                    pass
            if item.video and not item.is_address and "address" not in item.facts:     # a file: where it comes from
                try:
                    item.facts["address"] = media.info(Path(item.video))["source"]
                except (media.MediaError, OSError, subprocess.SubprocessError, ValueError):
                    pass
        run.changes.tell()
    threading.Thread(target=measure, daemon=True, name="project-durations").start()
