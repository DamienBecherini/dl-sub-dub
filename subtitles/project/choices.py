"""What a choice of the user does to a project or to one of its videos: the steps ticked with what they need, how far
the work goes, the settings written the way the rest of the work expects them, the settings a video's task takes.

Plain functions over the model, without the disk nor the service: the orchestrator calls them, the tests too.
"""
from typing import Callable

from .. import language, review, steps
from .model import Item, Stage, StageState

S = StageState

ORDER = [Stage.DOWNLOAD, Stage.SUBTITLES, Stage.DUBBING]     # each step needs the one before it, when not done

# The step a setting of a video belongs to (its tab): choosing one ticks that step for the video.
STEP_OF_SETTING = {"max_height": Stage.DOWNLOAD, "audio": Stage.DOWNLOAD, "site_subtitles": Stage.DOWNLOAD,
                   "language": Stage.SUBTITLES, "languages": Stage.SUBTITLES, "transcription": Stage.SUBTITLES,
                   "translator": Stage.SUBTITLES, "dub_language": Stage.DUBBING, "mkv": Stage.DUBBING, "replace": Stage.DUBBING}

LANGUAGE_KEYS = {"languages", "dub_language"}             # locked while a video is subtitled or dubbed (rules.locked)
RECHECK_KEYS = {"dub_language", "mkv", "languages", "language"}   # changed: what the disk shows done is looked at again


def stages_for(stop: str | None, download: bool = False) -> list[Stage]:
    """Steps ticked for a project that goes as far as `stop`."""
    return ([Stage.DOWNLOAD] if download else []) + [Stage.SUBTITLES] + ([Stage.DUBBING] if steps.goes_to(stop, "dubbing") else [])


def stop_for(stages: list[Stage], stop: str | None, mkv: bool | None = None) -> str:
    """How far the work goes for these steps: the MKV when the dubbing is ticked (unless only the dubbed track was
    asked: `mkv` False, or `stop` "dubbing" when `mkv` was not chosen), the subtitles otherwise."""
    if Stage.DUBBING in stages:
        if mkv is not None:
            return "mux" if mkv else "dubbing"
        return stop if stop in ("dubbing", "mux") else "mux"
    return stop if stop in ("validation", "translation") else "translation"


def tick_with_needs(item: Item, stage: Stage | None, on: bool):
    """Ticks a step of a video with the steps it needs (not done yet: the dubbing its subtitles, the subtitles of an
    address its download), or unticks it with the steps that need it (not done, not running): nothing ticked can wait
    for a step that will not run."""
    item.tick(stage, on)
    if stage is None:
        return
    pending = lambda s: item.state(s) not in (S.DONE, S.SKIPPED)       # noqa: E731
    i = ORDER.index(stage)
    if on:
        for before in reversed(ORDER[:i]):
            if not pending(before):
                break                                 # done (or not for this video): what comes before is not needed
            item.tick(before, True)
    elif pending(stage):
        for after in ORDER[i + 1:]:
            if pending(after) and item.state(after) not in (S.RUNNING, S.PAUSED):
                item.tick(after, False)


def origin_of(item: Item) -> str:
    """The address a video comes from: its source, or for a file the one read in it (yt-dlp writes it there) or given by
    the user (facts["address"]); "" when unknown."""
    return item.source if item.is_address else str(item.facts.get("address") or "")


def normalized(changes: dict) -> dict:
    """Settings written the way the rest of the work expects them ("FR, en " → ["fr", "en"], a yes or no as a bool)."""
    if changes.get("languages") is not None:
        changes = {**changes, "languages": language.normalize_codes(changes["languages"])}
    if "dub_language" in changes:              # one code or several: a list; nothing: the default
        codes = language.normalize_codes(steps.dub_codes(changes["dub_language"]))
        changes = {**changes, "dub_language": codes or None}
    if "pause" in changes:                     # only "also before a free translation"; "never", of older versions, is gone
        changes = {**changes, "pause": True if changes["pause"] is True else None}
    for key in ("mkv", "replace", "spend_agreed"):
        if changes.get(key) is not None:
            changes = {**changes, key: bool(changes[key])}
    if changes.get("review") is not None:     # which AI review: none, the names, every subtitle (True: the names)
        changes = {**changes, "review": review.mode(changes["review"])}
    if changes.get("review_engine") is not None:
        changes = {**changes, "review_engine": str(changes["review_engine"]) or None}      # "": the default one
    if "order" in changes:
        changes = {**changes, "order": "videos" if changes["order"] == "videos" else None}      # None: step by step
    return changes


# --- the settings of a video for its task: its own choice, otherwise the project's ---
def own(options: dict, item: Item, key: str):
    """A setting of the video: its own if it chose one, otherwise the project's `options` (engines, languages)."""
    return item.overrides[key] if item.overrides.get(key) not in (None, "") else options.get(key)


def dub_languages_of(options: dict, item: Item) -> list[str]:
    """The dubbed languages chosen for a video that will be dubbed (its own, otherwise the project's): they must be
    translated too. [] when nothing is chosen (the first translated language) or the video is not to be dubbed."""
    if not item.ticked(Stage.DUBBING) and item.state(Stage.DUBBING) != S.DONE:
        return []
    return steps.dub_codes(item.overrides.get("dub_language") or options.get("dub_language"))


def languages_of(options: dict, item: Item, default: Callable[[], list[str] | None]) -> list[str] | None:
    """The languages of the subtitles of a video: its own or the project's, and the dubbed languages it will speak (a
    voice reads its translation: translated first). None: those of the settings, nothing to add; `default()`: the
    languages of the settings, asked only when dubbed languages must be added to them."""
    base = own(options, item, "languages")
    dub = dub_languages_of(options, item)
    if not dub:
        return base
    return language.normalize_codes([*(base or default() or ["fr"]), *dub])


def until_of(item: Item, until):
    """Where the work of a video stops: the project's, but a video that chose its final file (an MKV or the dubbed
    voice alone) dubs to that one."""
    mine = item.overrides.get("mkv")
    if mine is None or until not in ("dubbing", "mux"):
        return until
    return "mux" if mine else "dubbing"
