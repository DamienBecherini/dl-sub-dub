"""Projects: the work of the page and of the command line, in four steps (Select → Download → Subtitles → Dubbing).

A project is an ordered list of items (an address to download, or a video file), the steps ticked for it, and its
settings. Each item knows, step by step, where it stands. What is really done is read from the disk, so that a
project survives a restart and a step already finished is never redone.

- `model`: Project, Item, Stage, StageState: plain data, saved as JSON; the state machine of a step (Event, TRANSITIONS).
- `settings`: the settings of an item, resolved from its own choices, those of the project, then the configuration.
- `stages`: one class per step (Strategy): does it apply to the item, what it needs to start.
- `rules`: what can be done now (the buttons of a line, what Run takes, what a step lacks): decided here, shown by the page.
- `store`: the project file next to the videos (Repository); an old batch file (`subtitles.batch.json`) is read too.
"""
from .model import STAGES, Event, Item, Project, Stage, StageState, StageStatus

__all__ = ["STAGES", "Event", "Item", "Project", "Stage", "StageState", "StageStatus"]
