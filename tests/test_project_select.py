"""Step 1, Select: entries of every kind turned into videos, the folder of a new project, and the actions of the page
of the four steps on a project (create without starting, add, tick, remove, run the ticked steps)."""
import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from subtitles.web import service
from subtitles.web import observed
from subtitles import config, source
from subtitles.project import Stage, StageState, sources
from subtitles.web import downloads, server
from subtitles.web.manager import Manager
from subtitles.project.choices import stop_for
from subtitles.web.tasks import TaskQueue

PLAYLIST = {"playlist": "My course", "entries": [{"url": "https://x.example/1", "title": "One", "duration": 60},
                                                 {"url": "https://x.example/2", "title": "Two", "duration": None}]}


@pytest.fixture
def fake_sites(monkeypatch):
    def describe(url, command):
        if url.endswith("/list"):
            return PLAYLIST
        if url.endswith("/gone"):
            raise source.SourceError("Video unavailable")
        return {"playlist": None, "entries": [{"url": url, "title": "Alone " + url[-1], "duration": 30}]}
    monkeypatch.setattr(source, "describe", describe)


def test_every_kind_of_entry(tmp_path, fake_sites):
    folder = tmp_path / "Season"
    (folder / "sub").mkdir(parents=True)
    for p in (folder / "a.mp4", folder / "sub" / "b.mkv", tmp_path / "c.mp4"):
        p.write_bytes(b"x")
    (tmp_path / "notes.pdf").write_bytes(b"x")
    lst = tmp_path / "Mine.txt"
    lst.write_text("https://x.example/v\n# comment\nhttps://x.example/list\n", encoding="utf-8")
    r = sources.resolve(["https://x.example/list", str(folder), (tmp_path / "c.mp4").as_posix(), str(lst), "https://x.example/gone",
                         str(tmp_path / "notes.pdf"), str(tmp_path / "absent.mp4"), "", "# comment", "https://x.example/1"], ["yt-dlp"])
    got = [(i["kind"], i["title"], i["origin"]) for i in r["items"]]
    assert got == [("address", "One", "My course"), ("address", "Two", "My course"), ("file", "a.mp4", "Season"),
                   ("file", "b.mkv", "Season"), ("file", "c.mp4", ""), ("address", "Alone v", "Mine")]   # duplicates kept once
    assert [e["entry"] for e in r["errors"]] == ["https://x.example/gone", str(tmp_path / "notes.pdf"), str(tmp_path / "absent.mp4")]
    assert r["title"] == ""                                       # several origins: no name proposed
    assert sources.resolve(["https://x.example/list"], ["yt-dlp"])["title"] == "My course"


def test_the_folder_of_a_new_project(tmp_path):
    cfg = {"output_dir": str(tmp_path / "out"), "youtube": {"download_dir": str(tmp_path / "dl")}}
    with_address = [{"source": "https://x.example/1"}, {"source": str(tmp_path / "v" / "a.mp4")}]
    assert sources.project_folder(with_address, "My: course", cfg) == tmp_path / "dl" / "My course"
    files = [{"source": str(tmp_path / "v" / "a.mp4")}, {"source": str(tmp_path / "v" / "s" / "b.mp4")}]
    assert sources.project_folder(files, "x", cfg) == tmp_path / "v"
    assert sources.default_location(files, cfg) == (tmp_path / "v", False)
    assert sources.default_location(with_address, cfg) == (tmp_path / "dl", True)
    # chosen: another base folder, with or without the subfolder named after the project
    assert sources.project_folder(with_address, "My: course", cfg, base=tmp_path / "x") == tmp_path / "x" / "My course"
    assert sources.project_folder(with_address, "My course", cfg, base=tmp_path / "x", named=False) == tmp_path / "x"
    assert sources.project_folder(files, "Mine", cfg, named=True) == tmp_path / "v" / "Mine"


def test_how_far_the_work_goes_for_the_steps_ticked():
    assert stop_for([Stage.SUBTITLES], None) == "translation"
    assert stop_for([Stage.SUBTITLES, Stage.DUBBING], "translation") == "mux"
    assert stop_for([Stage.DUBBING], "dubbing") == "dubbing"
    assert stop_for([Stage.SUBTITLES], "mux") == "translation"


@pytest.fixture
def g(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "load", lambda: {"output_dir": str(tmp_path / "out"), "youtube": {"command": ["yt-dlp"]}})
    monkeypatch.setattr(downloads.DownloadJob, "start", lambda self: None)
    g = Manager(TaskQueue(history=[]), start=False)
    monkeypatch.setattr(observed, "measure_durations", lambda *_a: None)
    return g


def test_create_without_starting_then_add_tick_remove_and_run(g, tmp_path):
    a = tmp_path / "a.mp4"
    a.write_bytes(b"x")
    run = g.projects.create(tmp_path.as_posix(), [{"source": a.as_posix(), "selected": False}], {}, "Mine", [Stage.SUBTITLES], start=False)
    assert run.state == "suspended" and run.options["stop"] == "translation" and not run.project.items[0].selected
    assert g.projects.add_items(run.id, [{"source": a.as_posix()}, {"source": "https://x.example/1", "title": "One", "duration": 60}]) == 1
    assert run.project.stages == [Stage.DOWNLOAD, Stage.SUBTITLES]       # an address came: its download is ticked
    first, address = run.project.items
    assert address.facts["duration"] == 60
    assert g.projects.select(run.id, {first.id: True}) and first.selected
    assert g.projects.select(run.id, {first.id: False}) and not first.selected
    g.projects.feed()
    assert address.state(Stage.DOWNLOAD) == StageState.TODO               # suspended: nothing starts
    assert g.projects.start(run.id, [Stage.DOWNLOAD, Stage.SUBTITLES, Stage.DUBBING])
    assert run.state == "running" and run.options["stop"] == "mux"
    g.projects.feed()
    assert address.state(Stage.DOWNLOAD) == StageState.RUNNING
    assert first.state(Stage.SUBTITLES) == StageState.TODO               # unticked: left aside
    assert g.projects.remove_items(run.id, [first.id, address.id]) == 1   # the address being downloaded stays
    assert [i.id for i in run.project.items] == [address.id]
    assert not g.projects.start(run.id)                                    # already running


def test_a_finished_project_runs_again_from_phase_1(g, tmp_path):
    run = g.projects.create(tmp_path.as_posix(), [], {}, start=False)
    run.state, run.phase = "done", 3
    assert g.projects.start(run.id) and (run.state, run.phase) == ("running", 1)


# --- routes ---

@pytest.fixture
def base(tmp_path, monkeypatch, fake_sites):
    cfg = {"output_dir": str(tmp_path / "out"), "youtube": {"command": ["yt-dlp"], "download_dir": str(tmp_path / "dl")},
           "transcriptions": [], "translators": []}
    monkeypatch.setattr(config, "load", lambda *_a, **_k: json.loads(json.dumps(cfg)))
    monkeypatch.setattr(service, "manager", Manager(TaskQueue(history=[]), start=False))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def call(url, method="GET", body=None):
    req = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(), method=method, headers={"X-Language": "en"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def test_routes_of_the_page_of_the_four_steps(base, tmp_path):
    code, d = call(base + "/api/sources", "POST", {"entries": ["https://x.example/list"]})
    assert code == 200 and len(d["items"]) == 2 and d["title"] == "My course"
    code, p = call(base + "/api/projects", "POST", {"items": d["items"], "stages": ["download", "subtitles"], "title": d["title"]})
    assert code == 201 and p["state"] == "suspended" and p["stages"] == ["download", "subtitles"]
    assert Path(p["root"]) == tmp_path / "dl" / "My course" and len(p["items"]) == 2
    code, r = call(base + f"/api/projects/{p['id']}/select", "POST", {"items": {p["items"][1]["id"]: False}})
    assert code == 200 and [i["selected"] for i in r["items"]] == [True, False] and len(r["videos"]) == 1
    code, r = call(base + f"/api/projects/{p['id']}/start", "POST", {})
    assert code == 200 and r["state"] == "running"
    assert call(base + f"/api/projects/{p['id']}/start", "POST", {})[0] == 409
    assert call(base + "/api/projects/unknown")[0] == 404
    assert call(base + "/api/projects", "POST", {"items": [{"source": str(tmp_path / "absent.mp4")}]})[0] == 400
    for page in ("/", "/v2"):                  # the page of the four steps; /v2 kept for the bookmarks
        with urllib.request.urlopen(base + page, timeout=10) as r:
            assert r.status == 200 and b'src="/static/v2/main.js"' in r.read()
    with urllib.request.urlopen(base + "/settings", timeout=10) as r:
        assert r.status == 200 and b'src="/static/settings.js"' in r.read()


def test_where_the_project_goes(base, tmp_path):
    code, d = call(base + "/api/sources/location", "POST", {"sources": ["https://x.example/1"]})
    assert code == 200 and Path(d["base"]) == tmp_path / "dl" and d["named"] is True and d["name"]
    video = tmp_path / "v" / "a.mp4"
    video.parent.mkdir()
    video.write_bytes(b"x")
    code, d = call(base + "/api/sources/location", "POST", {"sources": [str(video)]})
    assert Path(d["base"]) == tmp_path / "v" and d["named"] is False
    items = [{"source": "https://x.example/1"}]
    code, p = call(base + "/api/projects", "POST", {"items": items, "title": "Season 2",
                                                    "location": {"base": str(tmp_path / "mine"), "named": True}})
    assert code == 201 and Path(p["root"]) == tmp_path / "mine" / "Season 2"
    code, p = call(base + "/api/projects", "POST", {"items": [{"source": str(video)}], "title": "x",
                                                    "location": {"base": str(tmp_path / "other"), "named": False}})
    assert code == 201 and Path(p["root"]) == tmp_path / "other"
    code, e = call(base + "/api/projects", "POST", {"items": items, "location": {"base": "relative/folder", "named": True}})
    assert code == 400 and "full path" in e["error"]
    code, e = call(base + "/api/projects", "POST", {"items": items, "location": {"base": str(video), "named": False}})
    assert code == 400 and "not a folder" in e["error"]


def test_quick_title_from_the_sites_that_give_it(monkeypatch):
    asked = []

    class Answer:
        def __init__(self, body):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def read(self):
            return self.body

    def urlopen(url, timeout):
        asked.append(url)
        if "vimeo" in url:
            raise OSError("no network")
        return Answer(json.dumps({"title": " Colours "}).encode())

    monkeypatch.setattr(source.urllib.request, "urlopen", urlopen)
    assert source.quick_title("https://youtu.be/abc") == "Colours"
    assert asked[0].startswith("https://www.youtube.com/oembed?format=json&url=https%3A%2F%2Fyoutu.be%2Fabc")
    assert source.quick_title("https://www.dailymotion.com/video/x1") == "Colours"
    assert source.quick_title("https://vimeo.com/1") is None                 # network error: the details will give it
    assert source.quick_title("https://peertube.example/w/1") is None and len(asked) == 3   # other site: not asked
