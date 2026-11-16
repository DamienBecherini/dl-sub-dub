"""A video whose file is gone says so on its line; a video added twice says which line holds it (also the file an
address of the list downloaded)."""
import pytest

from helpers import at
from subtitles.web import service
from subtitles.web.routes import projects as project_routes
from subtitles.web import observed
from subtitles import config
from subtitles.project import Stage, StageState
from subtitles.web import downloads
from subtitles.web.manager import Manager
from subtitles.web.tasks import TaskQueue
from types import SimpleNamespace

URL = "https://x.example/a"


@pytest.fixture
def g(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "load", lambda: {"output_dir": str(tmp_path / "out"), "youtube": {"command": ["yt-dlp"]}})
    monkeypatch.setattr(downloads.DownloadJob, "start", lambda self: None)
    g = Manager(TaskQueue(history=[]), start=False)
    monkeypatch.setattr(observed, "measure_durations", lambda *_a: None)
    return g


def test_a_file_gone_is_said_on_its_line(g, tmp_path):
    f = tmp_path / "f.mp4"
    f.write_bytes(b"x")
    run = g.projects.create(tmp_path.as_posix(), [{"source": f.as_posix()}, {"source": URL}], {}, "P", [Stage.SUBTITLES], start=False)
    item, web = run.project.items
    assert "missing" not in run.summary()["items"][0]["facts"]
    f.unlink()
    assert "missing" not in run.summary()["items"][0]["facts"]              # looked for every few seconds only
    run.files_checked = 0
    s = run.summary()
    assert s["items"][0]["facts"]["missing"] is True and "missing" not in s["items"][1]["facts"]   # an address not downloaded: nothing to miss
    f.write_bytes(b"x")
    run.files_checked = 0
    assert "missing" not in run.summary()["items"][0]["facts"]              # back: no longer said


def test_adding_what_is_already_there_says_which_line(g, tmp_path, monkeypatch):
    monkeypatch.setattr(service, "manager", g)
    video = tmp_path / "001 - Squid.mp4"
    video.write_bytes(b"x")
    run = g.projects.create(tmp_path.as_posix(), [{"source": URL, "title": "Squid"}], {}, "P", [Stage.DOWNLOAD], start=False)
    web = run.project.items[0]
    web.video = video.as_posix()                                            # downloaded
    at(web, Stage.DOWNLOAD, StageState.DONE)
    other = tmp_path / "other.mp4"
    other.write_bytes(b"x")
    req = SimpleNamespace(json=lambda: {"items": [{"source": video.as_posix()}, {"source": other.as_posix()}]})
    status, r = project_routes.add_project_items(req, run.id)
    assert status == 200 and r["added"] == 1
    assert r["already"] == [{"source": video.as_posix(), "title": "Squid", "kind": "address"}]   # the file of the WEB line
    assert len(r["project"]["items"]) == 2


def test_the_address_a_file_of_the_project_comes_from_is_not_added_again(g, tmp_path, monkeypatch):
    monkeypatch.setattr(service, "manager", g)
    video = tmp_path / "Squid.mp4"
    video.write_bytes(b"x")
    run = g.projects.create(tmp_path.as_posix(), [{"source": video.as_posix(), "title": "Squid.mp4"}], {}, "P", [Stage.SUBTITLES], start=False)
    run.project.items[0].facts["address"] = URL                              # read in the file: yt-dlp wrote it there
    req = SimpleNamespace(json=lambda: {"items": [{"source": URL}, {"source": "https://x.example/b"}]})
    status, r = project_routes.add_project_items(req, run.id)
    assert status == 200 and r["added"] == 1                                # downloading it again would make a second copy
    assert r["already"] == [{"source": URL, "title": "Squid.mp4", "kind": "origin"}]
    assert [i["source"] for i in r["project"]["items"]] == [video.as_posix(), "https://x.example/b"]
