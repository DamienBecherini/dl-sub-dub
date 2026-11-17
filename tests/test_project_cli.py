"""The four steps from the command line (--stages): the names of the steps, the project made like on the page, the end."""
import pytest

from helpers import at
from subtitles.web import observed
from subtitles import config, project_cli, source
from subtitles.project import Stage, StageState, sources
from subtitles.states import RunState
from subtitles.web import downloads
from subtitles.web.manager import Manager
from subtitles.web.tasks import TaskQueue

FOLLOW = project_cli.follow                               # the real one (the fixture replaces it)


def test_the_names_of_the_steps():
    assert project_cli.parse_stages("dub,dl,sub") == [Stage.DOWNLOAD, Stage.SUBTITLES, Stage.DUBBING]
    assert project_cli.parse_stages("subtitles") == [Stage.SUBTITLES]
    with pytest.raises(ValueError):
        project_cli.parse_stages("sub,translate")


@pytest.fixture
def setup(tmp_path, monkeypatch):
    cfg = {"output_dir": str(tmp_path / "out"), "youtube": {"command": ["yt-dlp"], "download_dir": str(tmp_path / "dl")},
           "transcriptions": [], "translators": [{"id": "none", "type": "none"}], "active_translator": "none", "target_languages": ["fr"]}
    monkeypatch.setattr(config, "load", lambda *_a, **_k: cfg)
    monkeypatch.setattr(downloads.DownloadJob, "start", lambda self: None)
    found = {"items": [{"source": "https://x.example/1", "title": "One", "duration": 60, "kind": "address", "origin": "My list"},
                       {"source": str(tmp_path / "v" / "a.mp4"), "title": "a", "duration": 30, "kind": "file", "origin": ""}],
             "errors": [{"entry": "nowhere", "error": "not found"}], "title": "My list"}
    monkeypatch.setattr(sources, "resolve", lambda entries, command: found)
    g = Manager(TaskQueue(history=[]), start=False)
    monkeypatch.setattr(observed, "measure_durations", lambda *_a: None)
    seen = {}
    monkeypatch.setattr(project_cli, "follow", lambda g_, run, log, cfg_, ask=None: seen.setdefault("run", run) and 0)
    return cfg, g, seen


def test_a_project_like_on_the_page(setup, tmp_path):
    cfg, g, seen = setup
    lines = []
    assert project_cli.run(["https://x.example/list", "nowhere"], [Stage.DOWNLOAD, Stage.SUBTITLES], cfg, lines.append, manager=g) == 0
    run = seen["run"]
    assert run.root == (tmp_path / "dl" / "My list").as_posix() and run.project.title == "My list"
    assert run.project.stages == [Stage.DOWNLOAD, Stage.SUBTITLES] and run.state == RunState.RUNNING
    assert run.options["spend_agreed"] is True and any("nowhere" in x for x in lines)


def test_a_chosen_folder_and_files_only(setup, tmp_path, monkeypatch):
    cfg, g, seen = setup
    only_file = {"items": [{"source": str(tmp_path / "v" / "a.mp4"), "title": "a", "duration": 30, "kind": "file", "origin": ""}],
                 "errors": [], "title": ""}
    monkeypatch.setattr(sources, "resolve", lambda entries, command: only_file)
    project_cli.run(["x"], [Stage.DOWNLOAD, Stage.SUBTITLES, Stage.DUBBING], cfg, print, project_dir=str(tmp_path / "mine"), name="Season 2",
                    mkv=False, manager=g)
    run = seen["run"]
    assert run.root == (tmp_path / "mine" / "Season 2").as_posix()
    assert run.project.stages == [Stage.SUBTITLES, Stage.DUBBING] and run.options["stop"] == "dubbing"     # no address: no download


def test_video_by_video(setup):
    cfg, g, seen = setup
    project_cli.run(["x"], [Stage.SUBTITLES], cfg, print, order="videos", manager=g)
    assert seen["run"].options["order"] == "videos" and seen["run"].summary()["order"] == "videos"
    seen.clear()
    project_cli.run(["x"], [Stage.SUBTITLES], cfg, print, order="steps", manager=g)      # the same project, resumed
    assert "order" not in seen["run"].options and seen["run"].summary()["order"] == "steps"


def test_nothing_found_is_an_error(setup, monkeypatch):
    cfg, g, _ = setup
    monkeypatch.setattr(sources, "resolve", lambda entries, command: {"items": [], "errors": [], "title": ""})
    with pytest.raises(source.SourceError):
        project_cli.run(["x"], [Stage.SUBTITLES], cfg, print, manager=g)


def test_the_end_of_a_project(setup, tmp_path, monkeypatch):
    cfg, g, _ = setup
    a = tmp_path / "a.mp4"
    a.write_bytes(b"x")
    run = g.projects.create(tmp_path.as_posix(), [{"source": a.as_posix()}], {}, "Mine", [Stage.SUBTITLES], start=False)
    at(run.project.items[0], Stage.SUBTITLES, StageState.ERROR, error="no server")
    run.state = RunState.DONE
    lines = []
    assert FOLLOW(g, run, lines.append, cfg, wait_s=0) == 1
    assert "no server" in lines[-1]
