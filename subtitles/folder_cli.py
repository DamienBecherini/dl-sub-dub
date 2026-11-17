"""A whole folder from the command line: the same project as in the web interface (`dl-sub-dub.project.json`, resumed
after an interruption), run by the same task manager, without a server and without a pause before translation.

    python -m subtitles <folder | .project.json file> [options]

If the folder already has a project file (in the folder itself, or in the output folder when the "output folder"
destination is ticked), the command resumes it: finished videos are skipped, the others start again from the kept
transcription. `--new-project` starts again from an inventory of the folder.
"""
import time
from pathlib import Path

from . import folder, i18n, language, source
from .project import store
from .states import FINISHED_RUN_STATES, RunState, TaskState, VideoState

WAIT_S = 1.0


def is_folder_or_project(path: str) -> bool:
    p = Path(path)
    return p.is_dir() or (p.is_file() and p.name.endswith((".batch.json", ".project.json")))


def find_project_file(path: Path, cfg: dict, output_dir: str | None) -> Path | None:
    """Existing project (or old batch) file for this folder (or this file itself), None if there is none."""
    if path.is_file():
        return path
    return store.find(path, Path(output_dir or cfg["output_dir"]))


def run(path: str, cfg: dict, log, *, transcription: str | None = None, translator: str | None = None,
        spoken_language: str | None = None, languages: list[str] | None = None, output_dir: str | None = None, redo: bool = False,
        stop: str | None = None, new: bool = False, recursive: bool = True, manager=None) -> int:
    """Creates or resumes the project, follows it to the end while copying the log of the videos. 0 if all is done, 1 otherwise."""
    from .web.manager import Manager      # no import of the web package for a single video

    g = manager or Manager()
    p = Path(path.strip().strip('"'))
    file = None if new and p.is_dir() else find_project_file(p, cfg, output_dir)
    if file:
        run_ = g.projects.load(str(file))
        changes = {"translator": translator, "transcription": transcription, "spend_agreed": True, "stop": stop}
        if languages is not None:
            changes["languages"] = languages
        g.projects.resume(run_.id, changes)
        done = sum(1 for v in run_.videos if v["state"] == VideoState.DONE)
        log(i18n.t("log.cli_folder_resume", file=run_.file, done=done, n=len(run_.videos)))
    else:
        if not p.is_dir():
            raise source.SourceError(i18n.t("err.folder_not_found", path=p))
        videos = folder.scan(p, recursive)
        if not videos:
            raise source.SourceError(i18n.t("err.no_videos_in", path=p))
        options = {"transcription": transcription or "", "translator": translator or "", "spend_agreed": True, "redo": redo}      # the command is the go-ahead
        if languages is not None:
            options["languages"] = language.normalize_codes(languages)
        if output_dir:
            options["output_dir"] = output_dir
        if stop:
            options["stop"] = stop
        run_ = g.projects.create(str(p).replace("\\", "/"), [{"path": v["path"], "language": spoken_language or ""} for v in videos], options)
        log(i18n.t("log.cli_folder_new", n=len(videos), file=run_.file))
    try:
        return follow(g, run_, log)
    except KeyboardInterrupt:
        g.projects.suspend(run_.id)
        log(i18n.t("log.cli_interrupted_project", file=run_.file))
        return 130


def follow(g, run_, log, wait_s: float = WAIT_S) -> int:
    read: dict[str, int] = {}                   # task → log lines already displayed
    announced: set[str] = set()
    while True:
        if run_.state == RunState.AWAITING_TRANSLATION:   # should not happen without a pause; translate at once
            g.projects.translate(run_.id, None, None)
        finished = run_.state in FINISHED_RUN_STATES    # read before the log: the last lines are displayed
        several = run_.concurrency > 1
        for v in list(run_.videos):
            t = g.queue.get(v.get("task") or "")
            if t is None or t.state == TaskState.QUEUED:
                continue
            if t.id not in announced:
                announced.add(t.id)
                rank = run_.videos.index(v) + 1
                log(f"── {rank}/{len(run_.videos)} : {v['name']} ──")
            start = read.get(t.id, 0)
            for line in t.log[start:]:
                print(("  " + v["name"] + " " if several else "  ") + line, flush=True)
            read[t.id] = len(t.log)
        if finished:
            break
        time.sleep(wait_s)
    c = {e: sum(1 for v in run_.videos if v["state"] == e) for e in (VideoState.DONE, VideoState.ERROR, VideoState.CANCELLED)}
    log(i18n.t("log.cli_folder_finished" if run_.state == RunState.DONE else "log.cli_folder_cancelled", done=c[VideoState.DONE], errors=c[VideoState.ERROR],
               cancelled=c[VideoState.CANCELLED]))
    for v in run_.videos:
        if v["state"] == VideoState.ERROR:
            log(f"  {v['name']} : {v.get('error') or i18n.t('state.error')}")
    return 0 if run_.state == RunState.DONE and not c[VideoState.ERROR] else 1
