"""The four steps from the command line, as on the page: addresses, playlists, video files, folders and .txt lists
mixed in one project, and the steps chosen (`--stages dl,sub,dub`), run by the same orchestrator, without a server.

    python -m subtitles <entries…> --stages dl,sub,dub [--project-dir <folder>] [--name <name>] [options]

The project goes where the page puts it by default (the download folder and a subfolder named after the project
when there are addresses, otherwise the folder the files share), or in `--project-dir` (with a subfolder `--name`
if given). It does not pause before a translation (the command is the go-ahead). Ctrl+C suspends it: the same
command on the same folder resumes it (its project file).
"""
import time
from pathlib import Path

from . import config, i18n, language, source
from .project import Stage, sources, store
from .states import FINISHED_RUN_STATES, RunState, TaskState

STAGE_NAMES = {"dl": Stage.DOWNLOAD, "download": Stage.DOWNLOAD, "sub": Stage.SUBTITLES, "subtitles": Stage.SUBTITLES,
               "dub": Stage.DUBBING, "dubbing": Stage.DUBBING}
WAIT_S = 1.0
PROGRESS_STEP = 25                              # a line every 25 % of a part downloaded


def parse_stages(text: str) -> list[Stage]:
    """"dl,sub,dub" → the steps, in their order; an unknown name is an error."""
    out = []
    for name in (n.strip().lower() for n in text.split(",") if n.strip()):
        if name not in STAGE_NAMES:
            raise ValueError(i18n.t("err.unknown_stage", name=name, names="dl, sub, dub"))
        out.append(STAGE_NAMES[name])
    return [s for s in (Stage.DOWNLOAD, Stage.SUBTITLES, Stage.DUBBING) if s in out]


def run(entries: list[str], stages: list[Stage], cfg: dict, log, *, project_dir: str | None = None, name: str | None = None,
        transcription: str | None = None, translator: str | None = None, spoken_language: str | None = None,
        languages: list[str] | None = None, mkv: bool | None = None, order: str | None = None, manager=None, ask=input) -> int:
    from .web.manager import Manager      # no import of the web package for a single video

    found = sources.resolve(entries, source.ytdlp_command(cfg))
    for e in found["errors"]:
        log(i18n.t("log.cli_entry_skipped", entry=e["entry"], error=e["error"]))
    items = found["items"]
    if not items:
        raise source.SourceError(i18n.t("err.no_video_selected"))
    title = (name or "").strip() or found["title"] or source.list_title()
    root = sources.project_folder(items, title, cfg, base=project_dir or None, named=bool(name) if project_dir else None)
    has_addresses = any(source.is_url(i["source"]) for i in items)
    stages = [s for s in stages if s != Stage.DOWNLOAD or has_addresses]
    g = manager or Manager()
    existing = store.find(root, Path(cfg["output_dir"]))
    if existing:                                         # the same command again: the project resumes
        run_ = g.projects.load(str(existing))
        g.projects.add_items(run_.id, items)
        log(i18n.t("log.cli_project_resume", file=run_.file))
    else:
        options = {"transcription": transcription or "", "translator": translator or "", "pause": False}
        if languages is not None:
            options["languages"] = language.normalize_codes(languages)
        if spoken_language:
            options["language"] = spoken_language
        if mkv is not None:
            options["mkv"] = mkv
        run_ = g.projects.create(root.as_posix(), items, options, title, stages, start=False)
        log(i18n.t("log.cli_project_new", n=len(items), file=run_.file))
    if order:                                            # "videos": each one to its end, then the next (as on the page)
        g.projects.set_project_settings(run_.id, {"order": order})
    g.projects.set_stages(run_.id, stages)
    if run_.state == RunState.SUSPENDED:
        g.projects.resume(run_.id, {"pause": False})
    else:
        g.projects.start(run_.id, stages)
    try:
        return follow(g, run_, log, cfg, ask)
    except KeyboardInterrupt:
        g.projects.suspend(run_.id)
        log(i18n.t("log.cli_interrupted_project", file=run_.file))
        return 130


def follow(g, run_, log, cfg: dict, ask=input, wait_s: float = WAIT_S) -> int:
    """Prints the downloads (a line per part and per quarter), the log of each task, and asks to free the graphics
    card when the dubbing waits for it; returns 0 when everything ticked is done."""
    read: dict[str, int] = {}
    said: dict[str, tuple] = {}
    announced: set[str] = set()
    while True:
        if run_.state == RunState.AWAITING_TRANSLATION:
            g.projects.translate(run_.id, None, None)
        if run_.card.asked:
            translator = config.engine(cfg, "translators", "active_translator", run_.options.get("translator") or None)
            ask(i18n.t("cli.gpu_wait", name=translator.get("name") or translator.get("id")) + " ")
            g.projects.gpu_freed(run_.id)
        finished = run_.state in FINISHED_RUN_STATES
        for item in run_.project.items:
            name = item.title or Path(item.video or item.source).name
            d = g.projects.fetching.progress(item.id)
            if d:
                quarter = int((d["percent"] or 0) // PROGRESS_STEP)
                key = (d["phase"], d.get("part"), quarter)
                if said.get(item.id) != key:
                    said[item.id] = key
                    pct = f" {int(d['percent'])} %" if d["percent"] is not None else ""
                    phase = i18n.t("v2.dlp." + d["phase"], lang=language.name(d.get("language") or "") or d.get("language") or "?")
                    log(f"  ↓ {name} : {phase}{pct}")
            for stage in (Stage.SUBTITLES, Stage.DUBBING):
                t = g.queue.get(item.stages[stage].task or "")
                if t is None or t.state == TaskState.QUEUED:
                    continue
                if t.id not in announced:
                    announced.add(t.id)
                    log(f"── {i18n.t('v2.step.' + stage.value)} : {name} ──")
                for line in t.log[read.get(t.id, 0):]:
                    print("  " + line, flush=True)
                read[t.id] = len(t.log)
        if finished:
            break
        time.sleep(wait_s)
    states = [(item, s, item.state(s)) for item in run_.project.items for s in run_.project.stages if item.ticked(s)]
    errors = [(i, s) for i, s, st in states if st.value in ("error", "cancelled")]
    done = sum(1 for _, _, st in states if st.value in ("done", "skipped"))
    log(i18n.t("log.cli_project_end", done=done, n=len(states), errors=len(errors)))
    for item, s in errors:
        log(f"  {item.title or item.source} ({i18n.t('v2.step.' + s.value)}) : {item.stages[s].error or i18n.t('state.error')}")
    return 0 if run_.state == RunState.DONE and not errors else 1

