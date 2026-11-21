"""What can be done now, said in one place.

For each step of each video: does it apply, what does it lack to start, which buttons its line offers, would Run take
it. For the project: the steps Run would run, what is left, whether its languages are locked. The service decides with
these rules (a Run refused, a line started) and sends their result with the project (`view`); the page shows that
result and computes none of it. Written once, they cannot disagree: the page used to work them out again from the
states, and each difference was a button offering what the service refused, or the other way round.

Only reads: nothing here changes a project. What depends on the machine or on the run (is the dubbing set up, the
settings resolved down to the configuration) comes in a `Context`, so the rules are tested without a service.
"""
from dataclasses import dataclass
from enum import StrEnum
from typing import Callable, Mapping

from .. import steps
from .model import BUSY, FINISHED, LEFT, STAGES, Item, Project, Stage, StageState
from .stages import STRATEGIES

S = StageState


class Lack(StrEnum):
    """What a step needs that is not there."""
    DOWNLOAD = "download"                          # its video is not downloaded yet
    SUBTITLES = "subtitles"                        # not subtitled yet: the dubbing speaks the translated subtitles
    TRANSLATION = "no_translation"                 # subtitled, but nothing translated to speak
    SETUP = "dub_setup"                            # the dubbing is not set up on this machine
    NOTHING = "nothing_to_dub"                     # the only language asked is the one spoken in the video


# The step before brings it (Run does the steps in order): it only waits. The others cannot happen as things are.
COMES_WITH_THE_STEP_BEFORE = (Lack.DOWNLOAD, Lack.SUBTITLES)


@dataclass(frozen=True)
class Context:
    settings: Callable[[Item | None], Mapping]     # the settings of a video (or of the project), resolved
    dubbing: bool = False                          # the dubbing is set up (Settings)
    state: str = "suspended"                       # of the run: running, suspended, awaiting_translation, done, cancelled
    started: bool = False                          # some work was done or is under way (ProjectRun.started)
    launched: bool = False                         # Run was pressed on the project (a video run from its line does not count)
    sharing: str = ""                              # how the graphics card is shared (gpu.sharing)
    language: str = ""                             # the spoken language chosen for the project ("" = detected)
    finishing: frozenset = frozenset()             # ids of the videos that end after "pause after the running videos"
    agreed: frozenset = frozenset()                # ids of the videos that had the go-ahead and wait for their turn to be translated

    @property
    def closed(self) -> bool:
        """The project is cancelled: nothing starts any more."""
        return self.state == "cancelled"

    @property
    def running(self) -> bool:
        return self.state == "running"

    @property
    def suspended(self) -> bool:
        """Paused or stopped, or not run yet."""
        return self.state == "suspended"


# --- one video --------------------------------------------------------------------------------------------------

def site_language(item: Item) -> str:
    """The language its site gives ("fr-FR" → "fr"), known before any download."""
    return str((item.facts.get("site") or {}).get("language") or "").lower().replace("_", "-").split("-")[0]


def spoken(ctx: Context, item: Item) -> str:
    """The language spoken in a video: the one its transcription found (its files are named after it), otherwise the
    one chosen for it, the one its site gives, the one chosen for the project; "" when unknown (the default of the
    configuration is not counted: the task of a project detects the language unless the project says one)."""
    return item.facts.get("detected_language") or item.overrides.get("language") or site_language(item) or ctx.language


def dub_targets(ctx: Context, item: Item) -> list[str]:
    """The languages a video will be dubbed in (steps.dub_targets, as the dubbing itself): those chosen for it or for
    the project but its spoken one, otherwise every language of its subtitles but its spoken one."""
    s = ctx.settings(item)
    return steps.dub_targets(list(s["languages"] or []), spoken(ctx, item), s["dub_language"])


def applies(item: Item, stage: Stage) -> bool:
    return STRATEGIES[stage].applies(item) and item.state(stage) != S.SKIPPED


def lacks(ctx: Context, item: Item, stage: Stage) -> Lack | None:
    """What this step needs that is not there, whatever its ticks and its state; None when it could start."""
    if stage == Stage.DUBBING:
        if not ctx.dubbing:
            return Lack.SETUP
        if not dub_targets(ctx, item):
            return Lack.NOTHING
    need = STRATEGIES[stage].needs(item)
    return Lack(need) if need else None


def left(item: Item, stage: Stage) -> bool:
    """Ticked for this step, which is to do (or to do again after an error or a cancellation)."""
    return item.ticked(stage) and item.state(stage) in LEFT and applies(item, stage)


def ready(ctx: Context, item: Item, stage: Stage) -> bool:
    """Run of this step would take this video now."""
    return left(item, stage) and lacks(ctx, item, stage) is None


def busy(item: Item) -> bool:
    """A task or a download holds one of its steps: its line is neither removed nor started again."""
    return any(item.state(s) in BUSY for s in STAGES)


def locked(items: list[Item]) -> bool:
    """One of these videos is being subtitled or dubbed. Its task took its languages when it started: changing them
    now would leave it done without a language added, or still making one removed. So they are locked meanwhile
    (videos waiting for the go-ahead of their translation are not: they take the languages when they are translated)."""
    return any(i.state(s) == S.RUNNING for i in items for s in (Stage.SUBTITLES, Stage.DUBBING))


def can_run(ctx: Context, item: Item, stage: Stage) -> bool:
    """Run, Retry or Translate of this step, for this video alone, is possible now. A step done can be made again."""
    state = item.state(stage)
    if ctx.closed or state == S.RUNNING or not STRATEGIES[stage].applies(item) or (state == S.PAUSED and stage != Stage.SUBTITLES):
        return False
    return lacks(ctx, item, stage) is None


def actions(ctx: Context, item: Item, stage: Stage) -> list[str]:
    """The buttons of a line for this step, in order: "run", "translate_left" (transcribed already: only its
    translation is left), "translate" (it waits for the go-ahead), "retry", "one_at_a_time" (the card was full: free
    it between the translation and the dubbing), "cancel", "play". [] when it lacks something (`lacks` says what)."""
    status = item.stages[stage]
    if status.state == S.RUNNING:
        return [] if ctx.closed else ["cancel"]
    if status.state == S.DONE:
        return ["play"] if item.video else []
    if status.state == S.SKIPPED or ctx.closed or lacks(ctx, item, stage):
        return []
    if status.state == S.PAUSED:
        return ["translate", "cancel"]
    if status.state == S.ERROR:
        full = stage == Stage.DUBBING and "out of memory" in (status.error or "").lower() and ctx.sharing != "one_at_a_time"
        return [*(["one_at_a_time"] if full else []), "retry"]
    if status.state == S.CANCELLED:
        return ["retry"]
    return ["translate_left"] if stage == Stage.SUBTITLES and item.facts.get("transcribed") else ["run"]


def chains(ctx: Context, item: Item) -> list[list[Stage]]:
    """Several steps in a row for this video alone (the ▾ of its line): those left, in their order, as far as its
    subtitles, and as far as its dubbing when one can be made."""
    if ctx.closed:
        return []
    open_ = lambda s: item.state(s) not in (S.DONE, *BUSY)              # noqa: E731
    download = item.is_address and open_(Stage.DOWNLOAD) and not item.video
    subtitles = (download or bool(item.video)) and open_(Stage.SUBTITLES)
    dubbing = subtitles and ctx.dubbing and open_(Stage.DUBBING) and bool(dub_targets(ctx, item))
    first = [Stage.DOWNLOAD] if download else []
    return [*([[Stage.DOWNLOAD, Stage.SUBTITLES]] if download and subtitles else []),
            *([[*first, Stage.SUBTITLES, Stage.DUBBING]] if dubbing else [])]


def current(project: Project, item: Item) -> Stage | None:
    """The step a line acts on now: the first of the project's steps ticked on the line and not done (its button),
    otherwise the last one done (Open). A step outside the project's runs from the menu of the line."""
    ticked = [s for s in STAGES if applies(item, s) and (item.stages[s].selected or item.state(s) == S.DONE)
              and (project.wants(s) or item.state(s) != S.TODO)]                  # done: its result, ticked or not
    return next((s for s in ticked if item.state(s) != S.DONE), ticked[-1] if ticked else None)


def log_step(item: Item) -> Stage:
    """The step whose log is shown first: the one running, otherwise the last one that ran."""
    ran = [s for s in STAGES if applies(item, s) and item.state(s) not in (S.TODO, S.SKIPPED)]
    return next((s for s in ran if item.state(s) == S.RUNNING), ran[-1] if ran else Stage.SUBTITLES)


# --- the project ------------------------------------------------------------------------------------------------

def open_step(item: Item, stage: Stage) -> bool:
    """Ticked for this step, which is not finished (running and waiting included)."""
    return item.ticked(stage) and item.state(stage) not in FINISHED


def to_take(item: Item, stage: Stage) -> bool:
    """Ticked for this step, which Run still has to take: not finished, and not at work already (a video run on its
    own, from its line: Run would start nothing for it; one that waits for its go-ahead is still to take)."""
    return open_step(item, stage) and item.state(stage) != S.RUNNING


def at_work(project: Project) -> bool:
    """A step of a video is running, started by the project or on its own."""
    return any(i.state(s) == S.RUNNING for i in project.items for s in STAGES)


def alone(ctx: Context, project: Project) -> bool:
    """A video is at work on its own (run from its line, or resumed by a go-ahead) in a project that does not run.
    Not the videos that end after "pause after the running videos" (`ctx.finishing`): the project asked to pause, and
    says so while they end."""
    return ctx.state in ("done", "suspended") and any(
        i.state(s) == S.RUNNING for i in project.items if i.id not in ctx.finishing for s in STAGES)


def waiting(project: Project) -> list[Item]:
    """The ticked videos whose subtitles wait for the user: the cost of their AI review or of their translation to
    accept, the proposals of their review to decide."""
    return [i for i in project.selected() if i.state(Stage.SUBTITLES) == S.PAUSED]


def asks(ctx: Context, project: Project) -> bool:
    """The go-ahead is asked now, above the list, with what it costs: the project stopped for it, or it does not run
    and videos wait (one run on its own, from its line; those a pause left waiting before the go-ahead). While the
    project runs, the videos transcribed wait for the others: the go-ahead is asked once, for all of them. Those that
    had it and wait for their turn to be translated (`ctx.agreed`: a project paused meanwhile) ask nothing more."""
    return ctx.state == "awaiting_translation" or (
        ctx.state in ("suspended", "done") and any(i.id not in ctx.agreed for i in waiting(project)))


def ticked_steps(project: Project) -> list[Stage]:
    """The steps of the project: those ticked for at least one video (done or not), in the order they run. A column
    ticked is a step Run runs; nothing else says it."""
    return [s for s in STAGES if any(i.ticked(s) and applies(i, s) for i in project.items)]


def run_steps(project: Project) -> list[Stage]:
    """The steps Run runs: those ticked for the project (the download only with addresses), and the steps before them
    that ticked videos still need (a video to dub whose subtitles are not made: Subtitles too), so that nothing
    ticked waits for a step that will not run."""
    addresses = any(i.is_address for i in project.items)
    wanted = {s for s in project.stages if s != Stage.DOWNLOAD or addresses}
    for item in project.selected():
        for n, stage in enumerate(STAGES):
            if stage in wanted and open_step(item, stage):
                wanted.update(before for before in STAGES[:n] if open_step(item, before))
    return [s for s in STAGES if s in wanted]


def blocked(project: Project, active: list[Stage] | None = None) -> list[Item]:
    """Ticked addresses that cannot go on with the steps run (`active`, by default the project's): the first step
    they have left needs their video, and their download is neither ticked nor done. Said above the list, so that
    nothing waits silently (what a dubbing lacks is said in its own cell)."""
    out = []
    for item in project.selected():
        for stage in active or project.stages:
            if item.state(stage) in FINISHED or not applies(item, stage) or not item.ticked(stage):
                continue
            if stage != Stage.DOWNLOAD and item.state(stage) == S.TODO and not item.video:
                out.append(item)
            break
    return out


def status(ctx: Context, project: Project) -> str:
    """The state of a project in words: never run (Run not pressed: a video run from its line does not count), it is
    "ready", not "suspended"; with nothing left, "done". What waits for the user comes first: a project where a
    video waits for the go-ahead is "awaiting_translation", even while another one runs on its own. Then what a video
    run on its own does: a project that does not run (finished, paused, never run) where one is at work is "running"
    meanwhile. A project paused while its videos end stays "suspended" (see `alone`)."""
    if asks(ctx, project):
        return "awaiting_translation"
    if alone(ctx, project):
        return "running"
    if ctx.state == "done":                 # ended; a video ticked again or added since: ready (a cancelled one or an error ended too)
        new = any(i.ticked(s) and i.state(s) in (S.TODO, *BUSY) for i in project.selected() for s in project.stages)
        return "ready" if new else "done"
    if not ctx.suspended:
        return ctx.state
    nothing_left = all(not open_step(i, s) for i in project.selected() for s in project.stages)
    return "done" if nothing_left else "suspended" if ctx.launched else "ready"


def step_counts(ctx: Context, project: Project, stage: Stage) -> dict:
    """For "only this step" (the ▾ of Run): the videos it would take, those ticked for it that it cannot take yet,
    and why (a step run alone does not run the one before)."""
    rest = [lacks(ctx, i, stage) for i in project.items if left(i, stage) and not ready(ctx, i, stage)]
    if stage == Stage.DUBBING and not ctx.dubbing:
        waiting, why = len(rest), Lack.SETUP
    elif stage == Stage.DUBBING:
        waiting, why = sum(1 for lack in rest if lack != Lack.NOTHING), Lack.SUBTITLES
    else:
        waiting, why = len(rest), Lack.DOWNLOAD
    return {"ready": sum(1 for i in project.items if ready(ctx, i, stage)), "waiting": waiting, "why": why}


# --- what the page is sent ---------------------------------------------------------------------------------------

def item_view(ctx: Context, project: Project, item: Item) -> dict:
    """A video as the page shows it: what is saved of it (Item.to_dict), and for each step what these rules say."""
    d = item.to_dict()
    for stage in STAGES:
        lack = lacks(ctx, item, stage) if applies(item, stage) else None
        d["stages"][stage].update(
            applies=applies(item, stage), lacks=lack, blocking=bool(lack) and lack not in COMES_WITH_THE_STEP_BEFORE,
            left=left(item, stage), ready=ready(ctx, item, stage), actions=actions(ctx, item, stage),
            runnable=can_run(ctx, item, stage) and item.state(stage) != S.PAUSED)
    return {**d, "current": current(project, item), "log_step": log_step(item), "busy": busy(item), "locked": locked([item]),
            "spoken": spoken(ctx, item), "dub_targets": dub_targets(ctx, item), "chains": chains(ctx, item)}


def project_view(ctx: Context, project: Project) -> dict:
    """What these rules say of the project as a whole (the `rules` of its summary). `run.open`: the steps Run has
    something to take for, named on its button; none (and nothing to resume): the button waits, even while a video
    run on its own is at work. `run.resume`: "Resume", only on a project run then paused or stopped. `go_ahead`: the go-ahead is asked now (see `asks`)."""
    steps_run = run_steps(project)
    rows = project.selected()
    open_steps = [s for s in steps_run if any(to_take(i, s) for i in rows)]
    return {"status": status(ctx, project), "locked": locked(project.items),
            "working": ctx.running or at_work(project), "go_ahead": asks(ctx, project),
            "finished": sum(1 for i in rows if all(not i.stages[s].selected or i.state(s) in FINISHED for s in project.stages)),
            "run": {"steps": steps_run, "open": open_steps,
                    "resume": ctx.suspended and ctx.launched and any(ready(ctx, i, s) for s in (open_steps or steps_run) for i in project.items)},
            "steps": {s: step_counts(ctx, project, s) for s in STAGES}}
