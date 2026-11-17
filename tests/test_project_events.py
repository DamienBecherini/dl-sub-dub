"""The project pushed to the page (Server-Sent Events) as soon as it says it changed (nothing is built nor compared
meanwhile), and the polling of a tab left open not counted as activity."""
import json
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from subtitles.web import service
from subtitles.web.routes import projects as project_routes
from subtitles import config
from subtitles.web import idle, server
from subtitles.web.manager import Manager
from subtitles.web.runs import Changes
from subtitles.web.tasks import Task, TaskQueue


@pytest.fixture
def base(tmp_path, monkeypatch):
    cfg = {"output_dir": str(tmp_path / "out"), "transcriptions": [], "translators": []}
    monkeypatch.setattr(config, "load", lambda *_a, **_k: json.loads(json.dumps(cfg)))
    monkeypatch.setattr(service, "manager", Manager(TaskQueue(history=[]), start=False))
    monkeypatch.setattr(project_routes, "EVENTS_GROUP_S", 0.01)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def read_event(stream) -> dict:
    data = ""
    for raw in stream:
        line = raw.decode("utf-8").rstrip("\n")
        if line.startswith("data: "):
            data = line[6:]
        elif line == "" and data:
            return json.loads(data)


def test_the_project_is_pushed_when_it_changes(base, tmp_path):
    a = tmp_path / "a.mp4"
    a.write_bytes(b"x")
    run = service.manager.projects.create(tmp_path.as_posix(), [{"source": a.as_posix()}], {}, "Mine", start=False)
    with urllib.request.urlopen(f"{base}/api/projects/{run.id}/events", timeout=10) as stream:
        assert stream.headers["Content-Type"].startswith("text/event-stream")
        first = read_event(stream)
        assert first["id"] == run.id and first["title"] == "Mine"
        service.manager.projects.set_project_settings(run.id, {"review": True})
        assert read_event(stream)["options"]["review"] == "names"      # True, as the versions before wrote it: the names


def test_nothing_is_built_while_the_project_does_not_change(base, tmp_path):
    a = tmp_path / "a.mp4"
    a.write_bytes(b"x")
    run = service.manager.projects.create(tmp_path.as_posix(), [{"source": a.as_posix()}], {}, "Mine", start=False)
    for thread in threading.enumerate():                   # the durations measured in the background say "changed" when read
        if thread.name == "project-durations":
            thread.join()
    built, summary = [], run.summary
    run.summary = lambda: (built.append(1), summary())[1]
    with urllib.request.urlopen(f"{base}/api/projects/{run.id}/events", timeout=10) as stream:
        read_event(stream)
        time.sleep(0.6)                                    # (it was built and compared twice a second)
        assert len(built) == 1
        task = Task({"project": run.id})                     # a task of the project moves forward: something to show
        service.manager.projects.task_progressed(task)
        for _ in range(100):
            if len(built) > 1:
                break
            time.sleep(0.02)
        assert len(built) == 2


def test_a_change_is_never_lost_and_a_wait_ends_by_itself():
    c = Changes()
    seen = c.count
    t0 = time.monotonic()
    assert c.wait(seen, 0.1) == seen and time.monotonic() - t0 >= 0.09      # nothing: the wait ends by itself
    c.tell()                                               # told while nobody waited (the stream was sending)
    assert c.wait(seen, 5) == seen + 1 and time.monotonic() - t0 < 2
    threading.Timer(0.1, c.tell).start()
    t0 = time.monotonic()
    assert c.wait(seen + 1, 5) == seen + 2 and time.monotonic() - t0 < 2    # woken the moment it is told


def test_an_unknown_project_has_no_events(base):
    try:
        urllib.request.urlopen(f"{base}/api/projects/nope/events", timeout=10)
        raise AssertionError("expected 404")
    except urllib.error.HTTPError as e:
        assert e.code == 404


def test_polling_is_not_activity():
    assert not idle.is_activity("GET", "/api/projects/abc")
    assert not idle.is_activity("GET", "/api/projects/abc/events")
    assert not idle.is_activity("GET", "/api/dubbing/gpu-check")
    assert idle.is_activity("POST", "/api/projects/abc/start")
    assert idle.is_activity("GET", "/v2")
