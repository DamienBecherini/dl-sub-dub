"""The settings of an item: its own choices first, then those of the project, then the configuration.

`collections.ChainMap` looks a key up in each layer in turn and writes only to the first one: reading
`resolve(cfg, project, item)["languages"]` gives the languages chosen for this video if any, otherwise those of the
project, otherwise those of the configuration; nothing is copied, so a change of the project is seen by every item
that did not choose otherwise.

The keys (a layer may hold only some of them):
- `language`: spoken language ("" = detected).
- `languages`: languages of the subtitles wanted, besides the spoken one.
- `transcription`, `translator`: id of the engine ("" = the active one of the configuration).
- `destination`: {next_to_video, output_dir}: where the subtitle files go.
- `output_dir`: folder of the working files (.work) and of the copies.
- `pause`: True to stop before a free translation too, to see its estimate; a paid model always waits for a go-ahead
  (see `spend_agreed`). False, written by older versions to never stop, reads as not set.
- `review`: AI review before translating: "" (none), "names" (the proper names) or "full" (the names, then every
  subtitle of each video); `True`, written by older versions, reads as "names" (review.mode).
- `review_engine`: id of the translator that reviews ("" = a Claude translator if there is one, otherwise the
  translator of the project; see review.engine).
- `spend_agreed`: the user said, in the go-ahead panel of this project, not to be asked any more before a paid model
  (AI review, translation) runs; otherwise a paid model always waits for a go-ahead.
- `stop`: how far the work goes: "validation" (names reviewed), "translation" (subtitles), "dubbing" (a dubbed
  audio track) or "mux" (and an MKV with every track); the Dubbing step is ticked for the last two.
- `mkv`: with the dubbing, an MKV with every track (True) or the dubbed track alone (False); sets `stop` ("mux" or
  "dubbing") while the Dubbing step is ticked. None: the MKV.
- `replace`: the MKV replaces the original video.
- `dub_language`: language of the dubbed voice, one of the languages translated ("" = the first one).
- `max_height`: maximum height of a download (1080…).
- `audio`: audio tracks of the site to download, by language (None: the site's default one, the original).
- `site_subtitles`: subtitles of the site to download, [{language, auto}] (auto: automatic, in the spoken language).
- `redo`: transcribe and translate again what is already done.
- `order`: "steps" (every video through one step, then the next: the estimate of every translation before any is
  paid, each AI loaded once) or "videos" (each video to its end, then the next: a first one finished sooner; the
  project stops at each video that waits for the user, the graphics card shared again for each video).
"""
from collections import ChainMap

from .. import language, review
from .model import Item, Project

DEFAULT_DESTINATION = {"next_to_video": True, "output_dir": False}


def defaults(cfg: dict) -> dict:
    """The bottom layer: the configuration (config.toml merged with the user's config.json); [projects] holds the
    defaults of a project chosen in the Settings (order, pause, review, mkv, replace)."""
    p = cfg.get("projects") or {}
    flag = lambda key: p[key] if isinstance(p.get(key), bool) else None                 # noqa: E731 - absent: None
    return {"language": cfg.get("language") or "", "languages": language.normalize_codes(cfg.get("target_languages") or ["fr"]),
            "transcription": "", "translator": "", "destination": {**DEFAULT_DESTINATION, **(cfg.get("destination") or {})},
            "output_dir": cfg.get("output_dir") or "", "pause": True if p.get("pause") is True else None, "review": review.mode(p.get("review")),
            "review_engine": str(p.get("review_engine") or ""), "spend_agreed": False, "stop": "translation",
            "replace": bool(p.get("replace")), "mkv": flag("mkv"), "dub_language": "",
            "order": p.get("order") if p.get("order") in ("steps", "videos") else "steps",
            "max_height": (cfg.get("youtube") or {}).get("max_height"), "redo": False, "audio": None, "site_subtitles": []}


ITEM_KEYS = {"language", "languages", "max_height", "audio", "site_subtitles", "dub_language",   # what a video may choose for itself
             "transcription", "translator", "mkv", "replace", "review"}      # review: "none", "names" or "full" for this video
PROJECT_KEYS = ITEM_KEYS | {"transcription", "translator", "pause", "review", "review_engine", "spend_agreed", "replace", "destination", "mkv", "order"}
ORDERS = ("steps", "videos")


def resolve(cfg: dict, project: Project, item: Item | None = None) -> ChainMap:
    """Settings of an item (or of the project alone): a read-through view, nothing copied."""
    layers = [project.settings, defaults(cfg)]
    if item is not None:
        layers.insert(0, item.overrides)
    return ChainMap(*layers)


def override(item: Item, key: str, value):
    """Chooses a setting for this item only; None goes back to the setting of the project."""
    if value is None:
        item.overrides.pop(key, None)
    else:
        item.overrides[key] = value
