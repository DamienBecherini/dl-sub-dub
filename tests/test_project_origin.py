"""A video file and the site it comes from: its address read in the file (yt-dlp writes it) or given by the user; its
site read only when asked; tracks of the site added to the file, the video not downloaded again."""
import threading
from types import SimpleNamespace

import pytest

from subtitles.web import service
from subtitles.web import router
from subtitles.web.routes import projects as project_routes
from subtitles.web import background
from subtitles import config, media, source, tracks
from subtitles.project import Stage
from subtitles.web import downloads
from subtitles.web.manager import Manager
from subtitles.project.choices import origin_of
from subtitles.web.tasks import TaskQueue

URL = "https://www.example.org/watch?v=abc"
SITE = {"title": "Squid", "duration": 60, "uploader": "Chan", "upload_date": "20211124", "thumbnail": "", "language": "en",
        "heights": [1080], "audio": [{"language": "en", "kind": "original"}, {"language": "fr", "kind": "auto"}],
        "subtitles": ["en"], "auto_subtitles": ["en"]}


@pytest.fixture
def g(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "load", lambda: {"output_dir": str(tmp_path / "out"), "youtube": {"command": ["yt-dlp"]}})
    monkeypatch.setattr(downloads.DownloadJob, "start", lambda self: None)
    monkeypatch.setattr(source, "tool", lambda name: name)
    g = Manager(TaskQueue(history=[]), start=False)
    g.started = []
    monkeypatch.setattr(background, "spawn", lambda name, target, *args: g.started.append((target, args)))
    return g


def a_file(g, tmp_path, monkeypatch, address=URL):
    """A project of one video file whose tags hold `address` ("" for none)."""
    monkeypatch.setattr(source, "duration", lambda path: 60.0)
    monkeypatch.setattr(media, "info", lambda path: {"source": address})
    video = tmp_path / "f.mkv"
    video.write_bytes(b"x")
    run = g.projects.create(tmp_path.as_posix(), [{"source": video.as_posix()}], {}, "P", [Stage.SUBTITLES], start=False)
    for t in threading.enumerate():                        # the durations and the addresses, read in the background
        if t.name == "project-durations":
            t.join(timeout=5)
    return run, run.project.items[0]


def run_started(g):
    while g.started:
        target, args = g.started.pop(0)
        target(*args)


def test_the_address_is_read_in_the_file_and_its_site_only_when_asked(g, tmp_path, monkeypatch):
    run, f = a_file(g, tmp_path, monkeypatch)
    assert f.facts["address"] == URL and origin_of(f) == URL and "address_given" not in f.facts
    asked = []
    monkeypatch.setattr(tracks, "site_info", lambda url, command: asked.append(url) or dict(SITE))
    g.projects.fetching.feed_infos(run)
    assert not g.started and not asked                                      # nothing asked to the site by itself
    assert g.projects.fetching.read_info_again(run.id, f.id) and f.facts["site_wanted"]
    g.projects.fetching.feed_infos(run)
    run_started(g)
    assert asked == [URL] and f.facts["site"]["audio"][1]["language"] == "fr"


def test_a_file_without_an_address_takes_the_one_given(g, tmp_path, monkeypatch):
    run, f = a_file(g, tmp_path, monkeypatch, address="")
    assert origin_of(f) == "" and not g.projects.fetching.read_info_again(run.id, f.id)       # nothing to read
    assert not g.projects.fetching.add_tracks(run.id, f.id, ["fr"], [])
    assert not g.projects.fetching.set_origin(run.id, f.id, "ftp://x") and not g.projects.fetching.set_origin(run.id, f.id, "https://a b")
    assert g.projects.fetching.set_origin(run.id, f.id, f"  {URL} ") and f.facts["address"] == URL and f.facts["address_given"]
    f.facts.update(site=dict(SITE), site_wanted=True)
    assert g.projects.fetching.set_origin(run.id, f.id, URL)                           # the same one: back to before its site was read
    assert "site" not in f.facts and "site_wanted" not in f.facts and f.facts["address_given"]
    assert g.projects.fetching.set_origin(run.id, f.id, "")                            # forgotten: the file is read again
    assert "address_given" not in f.facts


def test_an_address_of_the_list_keeps_its_own(g, tmp_path, monkeypatch):
    run = g.projects.create(tmp_path.as_posix(), [{"source": URL}], {}, "P", [Stage.DOWNLOAD], start=False)
    a = run.project.items[0]
    assert origin_of(a) == URL and not g.projects.fetching.set_origin(run.id, a.id, "https://other.example/")


def test_tracks_of_the_site_added_to_a_file(g, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(tracks, "fetch_subtitles", lambda url, command, folder, name, subs: calls.append(("subs", url, name)))
    monkeypatch.setattr(tracks, "add_audio", lambda url, command, video, lang: calls.append(("audio", url, lang)))
    run, f = a_file(g, tmp_path, monkeypatch)
    f.facts["site"] = dict(SITE)
    assert g.projects.fetching.add_tracks(run.id, f.id, ["fr"], [{"language": "en", "auto": False}])
    run_started(g)
    assert calls == [("subs", URL, "f"), ("audio", URL, "fr")]
    assert f.facts["tracks"] == {"audio": ["en", "fr"], "subtitles": [{"language": "en", "auto": False}]} and "adding" not in f.facts


def test_the_route_of_the_address(g, tmp_path, monkeypatch):
    monkeypatch.setattr(service, "manager", g)
    run, f = a_file(g, tmp_path, monkeypatch, address="")
    status, summary = project_routes.set_item_origin(SimpleNamespace(json=lambda: {"address": URL}), run.id, f.id)
    assert status == 200 and summary["items"][0]["facts"]["address"] == URL
    with pytest.raises(router.RequestError):
        project_routes.set_item_origin(SimpleNamespace(json=lambda: {"address": "nope"}), run.id, f.id)
