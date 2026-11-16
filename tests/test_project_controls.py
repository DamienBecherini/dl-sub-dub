"""While a project runs: a video ticked for download starts without waiting for the task in progress (the downloads
have a thread of their own), and "Run this step" adds the videos left to do instead of being refused."""
from pathlib import Path

import pytest

from subtitles.web import background
from subtitles.web import observed
from subtitles import config
from subtitles.project import Stage, StageState
from subtitles.states import RunState
from subtitles.web import downloads
from subtitles.web.manager import Manager
from subtitles.web.tasks import TaskQueue

S = StageState
A, B = "https://x.example/a", "https://x.example/b"


@pytest.fixture
def g(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "load", lambda: {"output_dir": str(tmp_path / "out"), "youtube": {"command": ["yt-dlp"], "max_height": 1080}})
    monkeypatch.setattr(downloads.DownloadJob, "start", lambda self: None)       # never run: their state is enough
    g = Manager(TaskQueue(history=[]), start=False)
    monkeypatch.setattr(observed, "measure_durations", lambda *_a: None)
    monkeypatch.setattr(background, "spawn", lambda *_a: None)                   # no reading of the sites
    return g


def folder(tmp_path) -> Path:
    f = tmp_path / "list"
    f.mkdir(exist_ok=True)
    return f


def test_a_download_ticked_meanwhile_starts_without_the_queue(g, tmp_path):
    run = g.projects.create(folder(tmp_path).as_posix(), [{"source": A}, {"source": B}], {"pause": False}, title="List")
    a, b = run.project.items
    g.projects.select(run.id, {b.id: False}, Stage.DOWNLOAD)
    g.projects.fetching.feed_all()
    assert (a.state(Stage.DOWNLOAD), b.state(Stage.DOWNLOAD)) == (S.RUNNING, S.TODO)
    g.projects.select(run.id, {b.id: True}, Stage.DOWNLOAD)
    assert g.projects.fetching.poke.is_set()                                                # the thread of the downloads is woken
    g.projects.fetching.feed_all()                                                    # that thread, not the loop of the queue
    assert b.state(Stage.DOWNLOAD) == S.RUNNING
    assert g.queue.tasks == {}                                                    # nothing given to the queue


def test_the_downloads_join_a_project_running_other_steps(g, tmp_path):
    video = folder(tmp_path) / "local.mp4"
    video.write_bytes(b"x")
    run = g.projects.create(folder(tmp_path).as_posix(), [{"source": A}, {"path": video.as_posix()}], {"pause": False},
                           title="List", stages=[Stage.SUBTITLES])
    assert run.state == RunState.RUNNING and not run.wants(Stage.DOWNLOAD)
    g.projects.fetching.feed_all()
    assert run.project.items[0].state(Stage.DOWNLOAD) == S.TODO
    assert g.projects.start(run.id, [Stage.DOWNLOAD], only=True)
    assert run.wants(Stage.DOWNLOAD) and run.project.stages == [Stage.SUBTITLES]      # the diagram is not changed
    g.projects.fetching.feed_all()
    assert run.project.items[0].state(Stage.DOWNLOAD) == S.RUNNING


def test_run_this_step_while_running_starts_the_videos_left(g, tmp_path, monkeypatch):
    files = []
    for n in ("one", "two"):
        f = folder(tmp_path) / f"{n}.mp4"
        f.write_bytes(b"x")
        files.append({"path": f.as_posix()})
    run = g.projects.create(folder(tmp_path).as_posix(), files, {"pause": False}, title="List", stages=[Stage.SUBTITLES])
    monkeypatch.setattr(g.projects, "_concurrency", lambda _run: 1)
    g.projects.feed()
    one, two = run.project.items
    assert (one.state(Stage.SUBTITLES), two.state(Stage.SUBTITLES)) == (S.RUNNING, S.TODO)
    assert g.projects.start(run.id, [Stage.SUBTITLES], only=True)
    assert two.state(Stage.SUBTITLES) == S.RUNNING and len(g.queue.tasks) == 2
    assert not g.projects.start(run.id, [Stage.SUBTITLES], only=True)              # nothing left: refused as before
    assert not g.projects.start(run.id, [Stage.SUBTITLES])                         # the whole run: still refused while running
