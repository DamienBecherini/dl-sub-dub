"""Projects (successors of the projects): items and the state machine of their steps, settings in layers, the project
file and the old batch files read as projects. What can be done with them: test_project_rules.py."""
import json
from pathlib import Path

import pytest

from helpers import at
from subtitles.project import Event, Item, Project, Stage, StageState, settings, store
from subtitles.project.model import TRANSITIONS

CFG = {"language": "", "target_languages": ["fr", "en"], "output_dir": "", "youtube": {"max_height": 1080}}


def _video(tmp_path: Path, name: str = "a.mp4") -> str:
    p = tmp_path / name
    p.write_bytes(b"video")
    return p.as_posix()


# --- model ---

def test_a_file_needs_no_download_an_address_does():
    f, a = Item("D:/v/a.mp4"), Item("https://www.youtube.com/watch?v=x")
    assert f.video == "D:/v/a.mp4" and f.state(Stage.DOWNLOAD) == StageState.SKIPPED
    assert a.video == "" and a.state(Stage.DOWNLOAD) == StageState.TODO and a.is_address


def test_a_step_follows_what_happens_to_it():
    i = Item("D:/v/a.mp4")
    s = i.stages[Stage.SUBTITLES]
    assert i.apply(Stage.SUBTITLES, Event.START, task="t1")
    assert s.state == StageState.RUNNING and s.started and s.finished is None and s.task == "t1"
    assert i.apply(Stage.SUBTITLES, Event.FAIL, error="server down")
    assert s.state == StageState.ERROR and s.finished and s.error == "server down" and s.task == "t1"
    assert i.apply(Stage.SUBTITLES, Event.RESET) and s.state == StageState.TODO and s.task is None and s.error == ""
    assert i.apply(Stage.SUBTITLES, Event.START, task="t2") and i.apply(Stage.SUBTITLES, Event.WAIT) and s.state == StageState.PAUSED
    assert i.apply(Stage.SUBTITLES, Event.START) and i.apply(Stage.SUBTITLES, Event.FINISH) and s.state == StageState.DONE and s.task == "t2"
    assert i.apply(Stage.SUBTITLES, Event.START)                               # a step done is made again when asked


def test_an_event_that_means_nothing_in_a_state_changes_nothing():
    i = Item("D:/v/a.mp4")
    s = i.stages[Stage.SUBTITLES]
    for event in (Event.WAIT, Event.FINISH, Event.CANCEL, Event.PUT_BACK):         # nothing runs: nothing to end
        assert not i.can(Stage.SUBTITLES, event) and not i.apply(Stage.SUBTITLES, event) and s.state == StageState.TODO
    assert not i.apply(Stage.DOWNLOAD, Event.START) and i.state(Stage.DOWNLOAD) == StageState.SKIPPED    # a file is never downloaded
    i.apply(Stage.SUBTITLES, Event.START, task="t1")
    for event in (Event.RESET, Event.FOUND_DONE, Event.DROP):                    # at work: its task says how it ends
        assert not i.apply(Stage.SUBTITLES, event) and s.state == StageState.RUNNING
    i.apply(Stage.SUBTITLES, Event.CANCEL)
    finished = s.finished
    assert not i.apply(Stage.SUBTITLES, Event.PUT_BACK) and s.state == StageState.CANCELLED   # a Stop heard after the Cancel
    assert not i.apply(Stage.SUBTITLES, Event.FAIL, error="late") and s.error == ""           # its failure heard too late
    assert i.apply(Stage.SUBTITLES, Event.CANCEL) and s.finished == finished                  # cancelled twice: nothing new


def test_every_state_has_a_way_out_and_every_event_a_use():
    for state in StageState:
        assert any(state in row and row[state] != state for row in TRANSITIONS.values()), state
    assert set(TRANSITIONS) == set(Event)


def test_project_round_trip_through_json():
    p = Project("D:/v", [Item("D:/v/a.mp4", overrides={"language": "en"}), Item("https://x.example/v", title="V", selected=False)],
                {"languages": ["de"]}, [Stage.DUBBING, Stage.SUBTITLES], title="Mine")
    at(p.items[0], Stage.SUBTITLES, StageState.DONE)
    back = Project.from_dict(json.loads(json.dumps(p.to_dict())))
    assert back.to_dict() == p.to_dict()
    assert back.stages == [Stage.SUBTITLES, Stage.DUBBING]               # always in the order they run
    assert back.items[0].state(Stage.SUBTITLES) == StageState.DONE and not back.items[1].selected


# --- settings ---

def test_settings_item_then_project_then_configuration():
    item = Item("D:/v/a.mp4", overrides={"language": "en"})
    p = Project("D:/v", [item], {"languages": ["de"], "translator": "claude"})
    s = settings.resolve(CFG, p, item)
    assert (s["language"], s["languages"], s["translator"], s["max_height"]) == ("en", ["de"], "claude", 1080)
    p.settings["translator"] = "local"                                   # seen at once: nothing was copied
    assert s["translator"] == "local"
    settings.override(item, "languages", ["es"])
    assert s["languages"] == ["es"] and settings.resolve(CFG, p)["languages"] == ["de"]
    settings.override(item, "languages", None)
    assert s["languages"] == ["de"]
    assert settings.resolve(CFG, Project("D:/v"))["languages"] == ["fr", "en"]


# --- the project file ---

def test_save_and_load(tmp_path):
    p = Project(tmp_path.as_posix(), [Item(_video(tmp_path))], {"languages": ["fr"]})
    f = store.path_for(tmp_path, None, False)
    store.save(p, f)
    assert f.name == store.FILE_NAME and [x.name for x in tmp_path.iterdir()] == ["a.mp4", store.FILE_NAME]   # no temporary file left
    back = store.load(f)
    assert back.to_dict() == p.to_dict() and back.updated
    assert store.find(tmp_path) == f


def test_a_newer_file_is_refused(tmp_path):
    f = tmp_path / store.FILE_NAME
    f.write_text(json.dumps({"schema": 99, "folder": "x", "items": []}))
    with pytest.raises(store.ProjectFileError):
        store.load(f)


def test_the_file_of_the_output_folder_must_be_about_this_folder(tmp_path):
    out, a, b = tmp_path / "out", tmp_path / "a", tmp_path / "b"
    for d in (out, a, b):
        d.mkdir()
    store.save(Project(a.as_posix()), store.path_for(a, out, True))
    assert store.find(a, out) == out / store.FILE_NAME
    assert store.find(b, out) is None


def test_an_old_project_file_is_read_as_a_project(tmp_path):
    run = {"id": "b1", "root": "D:/v", "created": 1.0, "options": {"translator": "claude", "languages": ["fr"], "stop": "mux", "pause": True},
             "videos": [
                 {"path": "D:/v/1.mp4", "name": "1.mp4", "language": "en", "state": "done", "muxed": True, "duration": 60},
                 {"path": "D:/v/2.mp4", "name": "2.mp4", "language": "", "state": "translated", "translated": True},
                 {"path": "D:/v/3.mp4", "name": "3.mp4", "language": "", "state": "prepared", "count": 120},
                 {"path": "D:/v/4.mp4", "name": "4.mp4", "language": "", "state": "error", "error": "server down"},
                 {"path": "D:/v/5.mp4", "name": "5.mp4", "language": "", "state": "error", "error": "no GPU", "translated": True},
                 {"path": "D:/v/6.mp4", "name": "6.mp4", "language": "", "state": "cancelled"}]}
    f = tmp_path / store.OLD_FILE_NAME
    f.write_text(json.dumps(run))
    p = store.load(f)
    assert (p.id, p.folder, p.stages) == ("b1", "D:/v", [Stage.SUBTITLES, Stage.DUBBING])
    assert p.settings == {"translator": "claude", "languages": ["fr"], "pause": True, "stop": "mux"}
    states = [(i.state(Stage.DOWNLOAD), i.state(Stage.SUBTITLES), i.state(Stage.DUBBING)) for i in p.items]
    S = StageState
    assert states == [(S.SKIPPED, S.DONE, S.DONE), (S.SKIPPED, S.DONE, S.TODO), (S.SKIPPED, S.TODO, S.TODO),
                      (S.SKIPPED, S.ERROR, S.TODO), (S.SKIPPED, S.DONE, S.ERROR), (S.SKIPPED, S.CANCELLED, S.TODO)]
    assert p.items[0].overrides == {"language": "en"} and p.items[0].facts["duration"] == 60
    assert p.items[3].stages[Stage.SUBTITLES].error == "server down" and p.items[4].stages[Stage.DUBBING].error == "no GPU"
    assert store.find(tmp_path) == f


def test_a_project_without_dubbing():
    p = store.from_batch({"root": "D:/v", "options": {"stop": "translation"}, "videos": []})
    assert p.stages == [Stage.SUBTITLES] and p.settings["stop"] == "translation"


def test_what_older_versions_wrote_among_the_settings_leaves_them():
    """A project that ran, the card waited for: once flags among the settings (and so in its file); now fields."""
    d = Project("D:/v", [Item("D:/v/a.mp4")], {"languages": ["de"]}).to_dict()
    d.pop("ran")
    d["settings"].update(ran=True, gpu_wait=True, gpu_released=True, gpu_restore=True, gpu_wait_items=["x"])
    p = Project.from_dict(d)
    assert p.ran and p.settings == {"languages": ["de"]}
    assert Project.from_dict(p.to_dict()).ran and not Project("D:/v").ran
