"""Each step has its own ticks (four videos to download, two of them to subtitle); one step can be run alone from its
screen; one video can be run, retried, translated or cancelled for one step, whatever the state of the project."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from helpers import at
from subtitles.web import service
from subtitles.web import observed
from subtitles import config, dubbing
from subtitles.project import Item, Stage, StageState
from subtitles.web import downloads
from subtitles.web.manager import Manager
from subtitles.web.tasks import TaskQueue

S = StageState
A, B = "https://x.example/a", "https://x.example/b"


@pytest.fixture
def g(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "load", lambda: {"output_dir": str(tmp_path / "out"), "youtube": {"command": ["yt-dlp"]}})
    monkeypatch.setattr(downloads.DownloadJob, "start", lambda self: None)
    monkeypatch.setattr(dubbing, "available", lambda cfg: True)
    g = Manager(TaskQueue(history=[]), start=False)
    monkeypatch.setattr(observed, "measure_durations", lambda *_a: None)
    return g


def project(g, tmp_path, sources, stages=(Stage.DOWNLOAD, Stage.SUBTITLES)):
    return g.projects.create(tmp_path.as_posix(), [{"source": s} for s in sources], {"pause": False}, "P", list(stages), start=False)


def video(tmp_path, name):
    p = tmp_path / name
    p.write_bytes(b"x")
    return p.as_posix()


def test_ticks_per_step_and_the_tick_of_select():
    i = Item(A)
    i.tick(Stage.SUBTITLES, False)
    assert i.selected and i.ticked(Stage.DOWNLOAD) and not i.ticked(Stage.SUBTITLES)
    i.tick(None, False)                                    # Select: every step
    assert not i.selected and not i.ticked(Stage.DOWNLOAD)
    i.tick(Stage.DUBBING, True)                            # ticked again for one step: back in the work
    assert i.selected and i.ticked(Stage.DUBBING) and not i.ticked(Stage.SUBTITLES)
    f = Item("D:/v.mp4")
    f.tick(Stage.SUBTITLES, False)
    f.tick(Stage.DUBBING, False)
    assert not f.selected                                  # its download is skipped: it does not count


def test_four_downloaded_two_subtitled(g, tmp_path):
    a, b = video(tmp_path, "a.mp4"), video(tmp_path, "b.mp4")
    run = project(g, tmp_path, [a, b], [Stage.SUBTITLES])
    assert g.projects.select(run.id, {run.project.items[1].id: False}, Stage.SUBTITLES)
    assert g.projects.start(run.id)
    g.projects.feed()
    assert [i.state(Stage.SUBTITLES) for i in run.project.items] == [S.RUNNING, S.TODO]
    first = g.queue.get(run.project.items[0].stages[Stage.SUBTITLES].task)
    first.state = "done"
    g.projects.task_changed(first)
    g.projects.feed()
    assert run.project.items[1].state(Stage.SUBTITLES) == S.TODO and run.state == "done"   # unticked: left aside


def test_run_one_step_without_changing_the_diagram(g, tmp_path):
    run = project(g, tmp_path, [A, video(tmp_path, "f.mp4")])
    assert g.projects.start(run.id, [Stage.DOWNLOAD], only=True)
    assert run.project.stages == [Stage.DOWNLOAD, Stage.SUBTITLES] and run.active == [Stage.DOWNLOAD]
    g.projects.feed()
    a, f = run.project.items
    assert a.state(Stage.DOWNLOAD) == S.RUNNING and f.state(Stage.SUBTITLES) == S.TODO     # the subtitles are not run
    assert run.summary()["active"] == ["download"]
    g.projects.suspend(run.id)
    assert g.projects.start(run.id) and run.active is None                                 # the ticked steps again


def test_the_ticks_of_the_diagram(g, tmp_path):
    run = project(g, tmp_path, [A])
    assert g.projects.set_stages(run.id, [Stage.SUBTITLES, Stage.DUBBING])     # an address: its download comes with them
    assert run.project.stages == [Stage.DOWNLOAD, Stage.SUBTITLES, Stage.DUBBING] and run.options["stop"] == "mux" and run.state == "suspended"
    assert all(run.project.items[0].ticked(s) for s in run.project.stages)          # the steps of the project are the ticked ones
    g.projects.select(run.id, {run.project.items[0].id: False}, Stage.DUBBING)       # the column of the dubbing unticked
    assert run.project.stages == [Stage.DOWNLOAD, Stage.SUBTITLES] and run.options["stop"] == "translation"


def test_run_one_video_while_the_project_is_suspended(g, tmp_path, monkeypatch):
    run = project(g, tmp_path, [A, B, video(tmp_path, "f.mp4")])
    a, b, f = run.project.items
    a.tick(None, False)
    assert g.projects.run_item(run.id, a.id, Stage.DOWNLOAD)                                # the download, at once
    assert a.state(Stage.DOWNLOAD) == S.RUNNING and a.ticked(Stage.DOWNLOAD) and a.id in g.projects.fetching.downloads
    assert b.state(Stage.DOWNLOAD) == S.TODO                                               # nothing else started
    assert not g.projects.run_item(run.id, b.id, Stage.SUBTITLES)                           # not downloaded yet
    assert g.projects.run_item(run.id, f.id, Stage.SUBTITLES)
    t = g.queue.get(f.stages[Stage.SUBTITLES].task)
    assert t.options["source"] == f.video and t.options["pause"] is False and t.options.get("until") in (None, "translation")
    assert not g.projects.run_item(run.id, f.id, Stage.SUBTITLES)                           # already running
    assert run.state == "suspended"


def test_translate_retry_and_cancel_one_video(g, tmp_path):
    run = project(g, tmp_path, [video(tmp_path, "f.mp4")], [Stage.SUBTITLES])
    f = run.project.items[0]
    g.projects.run_item(run.id, f.id, Stage.SUBTITLES)
    t = g.queue.get(f.stages[Stage.SUBTITLES].task)
    t.state, t.prepared = "paused", SimpleNamespace(cues=[1], code="en", video=Path(f.video), source_srt=Path(f.video))  # waits
    g.projects.task_changed(t)
    assert f.state(Stage.SUBTITLES) == S.PAUSED
    assert g.projects.run_item(run.id, f.id, Stage.SUBTITLES)                               # Translate: the same task
    assert t.state == "queued" and f.stages[Stage.SUBTITLES].task == t.id
    assert g.projects.cancel_item(run.id, f.id, Stage.SUBTITLES) and t.cancel_requested
    t.state = "cancelled"
    g.projects.task_changed(t)
    assert f.state(Stage.SUBTITLES) == S.CANCELLED
    assert g.projects.run_item(run.id, f.id, Stage.SUBTITLES)                               # Retry: a new task
    assert f.stages[Stage.SUBTITLES].task != t.id and f.state(Stage.SUBTITLES) == S.RUNNING
    assert not g.projects.cancel_item(run.id, f.id, Stage.DOWNLOAD)                         # nothing to cancel there


def test_dub_one_video(g, tmp_path):
    run = project(g, tmp_path, [video(tmp_path, "f.mp4")], [Stage.SUBTITLES])
    f = run.project.items[0]
    at(f, Stage.SUBTITLES, S.DONE)
    f.facts["translated"] = True                    # subtitled and translated: it can be dubbed
    assert g.projects.run_item(run.id, f.id, Stage.DUBBING)
    t = g.queue.get(f.stages[Stage.DUBBING].task)
    assert t.options["until"] == "mux" and f.state(Stage.DUBBING) == S.RUNNING
    t.state = "done"
    g.projects.task_changed(t)
    assert f.state(Stage.DUBBING) == S.DONE


def test_routes_of_a_line(g, tmp_path, monkeypatch):
    import json
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer

    from subtitles.web import server
    monkeypatch.setattr(service, "manager", g)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"

    def call(path, body):
        req = urllib.request.Request(base + path, data=json.dumps(body).encode(), method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())
    try:
        run = project(g, tmp_path, [A, B])
        a, b = run.project.items
        code, p = call(f"/api/projects/{run.id}/select", {"items": {b.id: False}, "stage": "download"})
        assert code == 200 and p["items"][1]["stages"]["download"]["selected"] is False
        assert call(f"/api/projects/{run.id}/items/{a.id}/run", {"stage": "download"})[0] == 200
        assert call(f"/api/projects/{run.id}/items/{a.id}/run", {"stage": "download"})[0] == 409
        assert call(f"/api/projects/{run.id}/items/{a.id}/cancel", {"stage": "download"})[0] == 200
        assert call(f"/api/projects/{run.id}/stages", {"stages": ["subtitles"]})[1]["stages"] == ["download", "subtitles"]   # addresses
        assert call(f"/api/projects/{run.id}/start", {"stages": ["download"], "only": True})[1]["active"] == ["download"]
        assert call(f"/api/projects/{run.id}/items/{a.id}/run", {"stage": "nothing"})[0] == 400
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_the_settings_of_the_subtitles_as_they_will_apply(g, tmp_path, monkeypatch):
    cfg = {"output_dir": str(tmp_path / "out"), "target_languages": ["fr"], "active_transcription": "home", "active_translator": "none",
           "translators": [{"id": "none", "type": "none"}, {"id": "claude", "type": "claude", "model": "m"},
                           {"id": "local", "type": "openai", "url": "http://127.0.0.1:8080"}]}
    monkeypatch.setattr(config, "load", lambda: cfg)
    run = g.projects.create(tmp_path.as_posix(), [{"source": video(tmp_path, "f.mp4")}], {}, "P", [Stage.SUBTITLES], start=False)
    s = run.subtitle_settings()
    assert (s["transcription"], s["translator"], s["languages"], s["pause"], s["pause_chosen"]) == ("home", "none", ["fr"], False, False)
    assert g.projects.set_project_settings(run.id, {"translator": "claude", "languages": ["FR", " en "]})
    s = run.subtitle_settings()
    assert s["translator_paid"] and s["pause"] and run.options["languages"] == ["fr", "en"]      # paid: pause by default
    g.projects.set_project_settings(run.id, {"translator": "local"})
    assert not run.subtitle_settings()["pause"]                                                    # local model: free
    g.projects.set_project_settings(run.id, {"pause": True})
    assert run.subtitle_settings()["pause"] and run.subtitle_settings()["pause_chosen"]
    assert run.summary()["subtitles"]["translator"] == "local"
    f = run.project.items[0]
    assert g.projects.set_item_settings(run.id, f.id, {"languages": ["DE"], "language": "en"}) and f.overrides == {"languages": ["de"], "language": "en"}
    t = g.queue.get(g.projects.run_item(run.id, f.id, Stage.SUBTITLES) and f.stages[Stage.SUBTITLES].task)
    assert t.options["languages"] == ["de"] and t.options["language"] == "en" and t.options["translator"] == "local"


def test_the_languages_are_locked_while_a_video_is_at_work(g, tmp_path):
    """A video being subtitled or dubbed keeps the languages it started with: changing them meanwhile is refused (it
    would end done without a language added), for the project and for that video; the other settings still change."""
    run = project(g, tmp_path, [video(tmp_path, "f.mp4"), video(tmp_path, "g.mp4")], [Stage.SUBTITLES])
    f, other = run.project.items
    assert g.projects.set_project_settings(run.id, {"languages": ["fr", "en"]})
    assert g.projects.run_item(run.id, f.id, Stage.SUBTITLES) and f.state(Stage.SUBTITLES) == S.RUNNING
    assert not g.projects.set_project_settings(run.id, {"languages": ["fr", "en", "de"]})
    assert not g.projects.set_project_settings(run.id, {"dub_language": ["de"]})
    assert not g.projects.set_item_settings(run.id, f.id, {"languages": ["de"]})
    assert run.options["languages"] == ["fr", "en"] and "languages" not in f.overrides
    assert g.projects.set_item_settings(run.id, other.id, {"languages": ["de"]})      # a video not at work: its own languages
    assert g.projects.set_project_settings(run.id, {"mkv": False})                    # not a language: still changed
    at(f, Stage.SUBTITLES, S.PAUSED)                                                 # waiting for the go-ahead of its translation
    assert g.projects.set_project_settings(run.id, {"languages": ["fr", "en", "de"]})  # taken when it is translated


def test_run_runs_the_columns_ticked(g, tmp_path):
    """The steps of a project are the ones ticked for its videos: the column of the dubbing ticked after the project was
    made without it, Run dubs once the subtitles are made (17/11: Run did the subtitles and left the dubbing)."""
    run = project(g, tmp_path, [A, B])
    a, b = run.project.items
    assert run.project.stages == [Stage.DOWNLOAD, Stage.SUBTITLES] and not a.ticked(Stage.DUBBING)   # not asked: not ticked
    assert g.projects.select(run.id, {a.id: True}, Stage.DUBBING)                     # one box of the column
    assert run.project.stages == [Stage.DOWNLOAD, Stage.SUBTITLES, Stage.DUBBING] and run.options["stop"] == "mux"
    assert a.ticked(Stage.DUBBING) and not b.ticked(Stage.DUBBING)                     # only the one ticked
    assert g.projects.start(run.id)                                                    # Run, as the page does
    assert run.wants(Stage.DUBBING) and run.takes(a, Stage.DUBBING) and not run.takes(b, Stage.DUBBING)
    assert run.project.stages == [Stage.DOWNLOAD, Stage.SUBTITLES, Stage.DUBBING]


def test_a_project_of_the_first_version_is_read_with_its_steps(g, tmp_path):
    """Before schema 2 every video was ticked for every step, and the steps of the project said what Run ran: read back,
    a step outside the project is unticked where it has not started, the steps it needs stay."""
    import json
    run = project(g, tmp_path, [A])
    run.save()
    d = json.loads(run.file.read_text(encoding="utf-8"))
    d["schema"] = 1
    for i in d["items"]:
        for s in i["stages"].values():
            s["selected"] = True                                 # as version 1 wrote them
    d["stages"] = ["subtitles"]
    run.file.write_text(json.dumps(d), encoding="utf-8")
    again = g.projects.load(str(run.file))
    item = again.project.items[0]
    assert not item.ticked(Stage.DUBBING) and item.ticked(Stage.SUBTITLES) and item.ticked(Stage.DOWNLOAD)
    assert again.project.stages == [Stage.DOWNLOAD, Stage.SUBTITLES]


def test_new_videos_are_ticked_for_the_steps_of_the_project(g, tmp_path):
    run = project(g, tmp_path, [A], [Stage.DOWNLOAD, Stage.SUBTITLES, Stage.DUBBING])
    assert g.projects.add_items(run.id, [{"source": B}]) == 1
    assert run.project.items[1].ticked(Stage.DUBBING)
    g.projects.select(run.id, {i.id: False for i in run.project.items}, Stage.DUBBING)
    assert g.projects.add_items(run.id, [{"source": B + "2"}]) == 1
    new = run.project.items[2]
    assert not new.ticked(Stage.DUBBING) and new.ticked(Stage.SUBTITLES) and new.ticked(Stage.DOWNLOAD)
