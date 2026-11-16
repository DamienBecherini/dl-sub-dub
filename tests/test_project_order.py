"""The order of the work: step by step (every video subtitled, then every video dubbed) or video by video (each video
dubbed right after its subtitles, then the next one; no pause before the translation)."""
import pytest

from helpers import at
from subtitles.web import card
from subtitles.web import observed
from subtitles import config
from subtitles.project import Stage, StageState
from subtitles.states import TaskState
from subtitles.web import downloads
from subtitles.web.manager import Manager
from subtitles.web.tasks import TaskQueue

S = StageState


@pytest.fixture
def g(tmp_path, monkeypatch):
    cfg = {"output_dir": str(tmp_path / "out"), "youtube": {"command": ["yt-dlp"]}, "target_languages": ["fr"],
           "dubbing": {"enabled": True, "command": ["python"], "model": "k2-fsa/OmniVoice", "steps": 32}}
    monkeypatch.setattr(config, "load", lambda *_a, **_k: cfg)
    monkeypatch.setattr(downloads.DownloadJob, "start", lambda self: None)
    g = Manager(TaskQueue(history=[]), start=False)
    monkeypatch.setattr(observed, "measure_durations", lambda *_a: None)
    monkeypatch.setattr(card, "ready", lambda run, wait: True)
    return g


def project(g, tmp_path, order):
    videos = []
    for n in ("one", "two"):
        f = tmp_path / f"{n}.mp4"
        f.write_bytes(b"x")
        videos.append({"source": f.as_posix()})
    run = g.projects.create(tmp_path.as_posix(), videos, {}, "Order", [Stage.SUBTITLES, Stage.DUBBING], start=False)
    assert g.projects.set_project_settings(run.id, {"order": order, "pause": True})
    assert g.projects.start(run.id)
    return run


def subtitled(g, item):
    """The subtitles task of the item ends (translated)."""
    g.queue.get(item.stages[Stage.SUBTITLES].task).state = TaskState.DONE
    at(item, Stage.SUBTITLES, S.DONE)
    item.facts["translated"] = True


def test_video_by_video_dubs_each_one_before_the_next(g, tmp_path):
    run = project(g, tmp_path, "videos")
    one, two = run.project.items
    assert run.summary()["order"] == "videos"
    g.projects.feed()
    assert (one.state(Stage.SUBTITLES), two.state(Stage.SUBTITLES)) == (S.RUNNING, S.TODO)
    assert g.queue.get(one.stages[Stage.SUBTITLES].task).options["pause"] is False     # no pause before the translation
    subtitled(g, one)
    g.projects.feed()
    assert one.state(Stage.DUBBING) == S.RUNNING and two.state(Stage.SUBTITLES) == S.TODO
    g.queue.get(one.stages[Stage.DUBBING].task).state = TaskState.DONE
    at(one, Stage.DUBBING, S.DONE)
    g.projects.feed()
    assert two.state(Stage.SUBTITLES) == S.RUNNING


def test_step_by_step_subtitles_every_video_first(g, tmp_path):
    run = project(g, tmp_path, "steps")
    one, two = run.project.items
    assert run.summary()["order"] == "steps" and "order" not in run.options     # the default is not written
    g.projects.feed()
    assert g.queue.get(one.stages[Stage.SUBTITLES].task).options["pause"] is True
    subtitled(g, one)
    g.projects.feed()
    assert one.state(Stage.DUBBING) == S.TODO and two.state(Stage.SUBTITLES) == S.RUNNING
