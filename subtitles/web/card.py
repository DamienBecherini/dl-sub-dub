"""The graphics card of a project, shared by a local translation model and the dubbing (see gpu.py): freed before a
dubbing (by itself, or by the user when nothing else can), given back to the model once the dubbing is over or when a
translation needs it again. What the project knows of it lives in `ProjectRun.card`, never in the project file."""
import logging

from .. import config, gpu
from ..states import RunState
from . import background
from .runs import ProjectRun

LOG = logging.getLogger(__name__)


def _translator(run: ProjectRun, cfg: dict) -> dict | None:
    try:
        return config.engine(cfg, "translators", "active_translator", run.options.get("translator") or None)
    except config.ConfigError:
        return None


def ready(run: ProjectRun, wait: bool) -> bool:
    """The card can take the dubbing: shared, freed now (free_gpu, Ollama), or freed by the user ("It is done").
    Otherwise False, and with `wait` the project waits for the user (suspended, run.card.wait)."""
    if run.card.released:
        return True
    try:
        cfg = config.load()
    except config.ConfigError:
        return True
    translator = _translator(run, cfg)
    way = gpu.how_to_free(cfg, translator)
    if not way:
        return True
    if way != "ask" and gpu.free(cfg, translator, log=LOG.info):
        run.card.released = True
        run.card.restore = run.card.restore or way == "hooks"       # started again once the dubbing is over
        return True
    run.card.wait = True
    if wait and run.state == RunState.RUNNING:
        run.state = RunState.SUSPENDED
    run.save()
    return False


def give_back(run: ProjectRun, now: bool = False):
    """The card goes back to the translation model (free_gpu.start), if this project freed it: in the background once
    the dubbing of the project is over, `now` when a translation is about to need it."""
    restore, run.card.released, run.card.restore = run.card.restore, False, False
    if not restore:
        return
    if now:
        gpu.restore(config.load(), LOG.info)
    else:
        background.spawn(f"gpu-{run.id}", gpu.restore, config.load(), LOG.info)
