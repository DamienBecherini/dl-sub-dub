"""The steps of an item, one class each (Strategy): the orchestrator handles them all the same way.

Each step says:
- `applies(item)`: whether it concerns this item at all (a video file needs no download);
- `needs(item)`: what it needs that is not there yet, as a word of `rules.Lack` ("" when it can start): the subtitles
  need the video file, the dubbing the subtitles translated;
- `resource`: what it occupies while it runs: the network, the transcription server, the graphics card.

What can be done now with these steps (the buttons of a line, what Run takes) is in `rules`; whether a step is already
done is read from the disk by web/observed.py (`steps.observe`).
"""
from typing import Protocol

from .model import Item, Stage, StageState


class StageStrategy(Protocol):
    stage: Stage
    resource: str

    def applies(self, item: Item) -> bool: ...
    def needs(self, item: Item) -> str: ...

    def can_start(self, item: Item) -> bool: ...


class _Strategy:
    def needs(self, item: Item) -> str:
        return ""

    def can_start(self, item: Item) -> bool:
        return not self.needs(item)


class DownloadStage(_Strategy):
    stage, resource = Stage.DOWNLOAD, "network"

    def applies(self, item: Item) -> bool:
        return item.is_address


class SubtitlesStage(_Strategy):
    """Transcription, review of the names, translation into the languages wanted."""
    stage, resource = Stage.SUBTITLES, "transcription"

    def applies(self, item: Item) -> bool:
        return True

    def needs(self, item: Item) -> str:
        return "" if item.video else "download"    # known: a file, or an address once downloaded


class DubbingStage(_Strategy):
    """Dubbed voice in the languages wanted, then an MKV with every track. It speaks the translated subtitles: a video
    starts only once subtitled and translated (its subtitles are never started from here, a translation may be paid)."""
    stage, resource = Stage.DUBBING, "gpu"

    def applies(self, item: Item) -> bool:
        return True

    def needs(self, item: Item) -> str:
        if item.state(Stage.SUBTITLES) != StageState.DONE:
            return "subtitles"                     # which wait for the video themselves, when it is not downloaded
        if not item.video:
            return "download"
        return "no_translation" if item.facts.get("translated") is False else ""


STRATEGIES: dict[Stage, StageStrategy] = {Stage.DOWNLOAD: DownloadStage(), Stage.SUBTITLES: SubtitlesStage(), Stage.DUBBING: DubbingStage()}
