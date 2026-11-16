"""The ▾ menu of a video: Download + Subtitle + Dub, Subtitle + Dub: the steps one after the other for this video
alone, each started when the one before ends; an error or a cancellation ends the chain."""
from types import SimpleNamespace

import pytest

from subtitles.web import service
from subtitles.web.routes import projects as project_routes
from subtitles.web import card
from subtitles.web import observed
from subtitles import config, dubbing
from subtitles.project import Stage, StageState
from subtitles.web import downloads
from subtitles.web.manager import Manager
from subtitles.web.tasks import TaskQueue

S = StageState
A = "https://x.example/a"


@pytest.fixture
def g(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "load", lambda: {"output_dir": str(tmp_path / "out"), "youtube": {"command": ["yt-dlp"]}})
    monkeypatch.setattr(downloads.DownloadJob, "start", lambda self: None)
    monkeypatch.setattr(dubbing, "available", lambda cfg: True)
    g = Manager(TaskQueue(history=[]), start=False)
    monkeypatch.setattr(observed, "measure_durations", lambda *_a: None)
    monkeypatch.setattr(observed, "observe", lambda *_a, **_k: None)       # what the disk says: not here
    monkeypatch.setattr(observed, "mark_transcribed", lambda *_a: None)
    monkeypatch.setattr(card, "ready", lambda *_a, **_k: True)
    return g


def project(g, tmp_path, sources):
    return g.projects.create(tmp_path.as_posix(), [{"source": s} for s in sources], {"pause": False}, "P",
                            [Stage.DOWNLOAD, Stage.SUBTITLES], start=False)


def downloaded(g, run, item, tmp_path, error=""):
    video = tmp_path / "a.mp4"
    video.write_bytes(b"x")
    job = SimpleNamespace(item_id=item.id, video=None if error else video, cancelled=False, stopped=False, error=error, log=[])
    g.projects.fetching.download_ended(run, job)


def subtitled(g, item, state="done"):
    t = g.queue.get(item.stages[Stage.SUBTITLES].task)
    item.facts["translated"] = True
    t.state = state
    g.projects.task_changed(t)


def test_download_subtitle_and_dub_one_after_the_other(g, tmp_path):
    run = project(g, tmp_path, [A])
    a = run.project.items[0]
    assert g.projects.run_chain(run.id, a.id, [Stage.DOWNLOAD, Stage.SUBTITLES, Stage.DUBBING])
    assert a.state(Stage.DOWNLOAD) == S.RUNNING and a.facts["then"] == ["subtitles", "dubbing"]
    assert a.ticked(Stage.DUBBING)                                          # ticked for every step of the chain
    downloaded(g, run, a, tmp_path)
    assert a.state(Stage.SUBTITLES) == S.RUNNING and a.facts["then"] == ["dubbing"]
    subtitled(g, a)
    assert a.state(Stage.DUBBING) == S.RUNNING and not a.facts.get("then")
    assert run.state == "suspended"                                         # the project itself is not run


def test_subtitle_then_dub_a_file(g, tmp_path):
    f = tmp_path / "f.mp4"
    f.write_bytes(b"x")
    run = project(g, tmp_path, [f.as_posix()])
    item = run.project.items[0]
    assert g.projects.run_chain(run.id, item.id, [Stage.SUBTITLES, Stage.DUBBING])
    assert item.state(Stage.SUBTITLES) == S.RUNNING
    subtitled(g, item, "paused")                                            # its translation waits: the chain too
    assert item.state(Stage.DUBBING) == S.TODO and item.facts["then"] == ["dubbing"]
    subtitled(g, item)
    assert item.state(Stage.DUBBING) == S.RUNNING


def test_a_failed_step_ends_the_chain(g, tmp_path):
    run = project(g, tmp_path, [A])
    a = run.project.items[0]
    g.projects.run_chain(run.id, a.id, [Stage.DOWNLOAD, Stage.SUBTITLES, Stage.DUBBING])
    downloaded(g, run, a, tmp_path, error="no network")
    assert a.state(Stage.DOWNLOAD) == S.ERROR and a.state(Stage.SUBTITLES) == S.TODO and "then" not in a.facts


def test_a_chain_that_cannot_start_leaves_nothing(g, tmp_path):
    run = project(g, tmp_path, [A])
    a = run.project.items[0]
    assert not g.projects.run_chain(run.id, a.id, [Stage.SUBTITLES, Stage.DUBBING])     # not downloaded yet
    assert "then" not in a.facts and a.state(Stage.SUBTITLES) == S.TODO


def test_the_route_takes_several_steps(g, tmp_path, monkeypatch):
    monkeypatch.setattr(service, "manager", g)
    run = project(g, tmp_path, [A])
    a = run.project.items[0]
    req = SimpleNamespace(json=lambda: {"stages": ["download", "subtitles"]})
    status, _ = project_routes.act_on_project_item(req, run.id, a.id, "run")
    assert status == 200 and a.state(Stage.DOWNLOAD) == S.RUNNING and a.facts["then"] == ["subtitles"]
