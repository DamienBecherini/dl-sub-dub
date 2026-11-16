""""Retry" a video in error of a project, whatever the state of the project."""

from subtitles.web import last_project
from helpers import Run
from subtitles.web.manager import Manager
from subtitles.web.tasks import TaskQueue


def manager(tmp_path, monkeypatch):
    monkeypatch.setattr(last_project, "FILE", tmp_path / "last_project.json")
    g = Manager(TaskQueue(history=[]), start=False)
    return g


def a_project(g, tmp_path, states, state="running", phase=1, options=None):
    videos = [{"path": f"/v{i}.mp4", "name": f"v{i}.mp4", "language": "", "state": e, "task": f"t{i}", "count": 10, "detected_language": "",
               "error": "Pas de réponse" if e == "error" else ""} for i, e in enumerate(states)]
    run = Run("/r", videos, options or {"pause": True}, tmp_path / "subtitles.batch.json")
    run.state, run.phase = state, phase
    g.projects.all[run.id] = run
    return run


def test_the_video_starts_from_scratch(tmp_path, monkeypatch):
    g = manager(tmp_path, monkeypatch)
    run = a_project(g, tmp_path, ["prepared", "error", "todo"])
    assert g.projects.retry(run.id, "/v1.mp4")
    v = run.videos[1]
    assert (v["state"], v["task"], v["error"]) == ("todo", None, "")
    assert g.queue.wake.is_set()


def test_a_single_video_targeted(tmp_path, monkeypatch):
    g = manager(tmp_path, monkeypatch)
    run = a_project(g, tmp_path, ["error", "error"])
    g.projects.retry(run.id, "/v0.mp4")
    assert [v["state"] for v in run.videos] == ["todo", "error"]


def test_every_video_in_error(tmp_path, monkeypatch):
    g = manager(tmp_path, monkeypatch)
    run = a_project(g, tmp_path, ["error", "prepared", "error"])
    g.projects.retry(run.id)
    assert [v["state"] for v in run.videos] == ["todo", "prepared", "todo"]


def test_suspended_project_stays_suspended(tmp_path, monkeypatch):
    g = manager(tmp_path, monkeypatch)
    run = a_project(g, tmp_path, ["error"], state="suspended")
    assert g.projects.retry(run.id)
    assert run.state == "suspended"


def test_finished_project_starts_again(tmp_path, monkeypatch):
    g = manager(tmp_path, monkeypatch)
    run = a_project(g, tmp_path, ["done", "error"], state="done", phase=2)
    run.finished = 123.0
    assert g.projects.retry(run.id)
    assert (run.state, run.finished) == ("running", None)


def test_project_awaiting_translation_finishes_this_video_first(tmp_path, monkeypatch):
    g = manager(tmp_path, monkeypatch)
    run = a_project(g, tmp_path, ["prepared", "error"], state="awaiting_translation")
    assert g.projects.retry(run.id)
    assert (run.state, run.phase) == ("running", 1)
    assert run.next_item() is run.items()[1]


def test_refused_without_video_in_error_or_cancelled_project(tmp_path, monkeypatch):
    g = manager(tmp_path, monkeypatch)
    assert not g.projects.retry(a_project(g, tmp_path, ["prepared"]).id)
    assert not g.projects.retry(a_project(g, tmp_path, ["error"], state="cancelled").id)
    assert not g.projects.retry("unknown")


def test_phase_1_starts_again_with_the_project_options(tmp_path, monkeypatch):
    g = manager(tmp_path, monkeypatch)
    run = a_project(g, tmp_path, ["error"])
    g.projects.retry(run.id)
    g.projects.feed()
    t = next(iter(g.queue.tasks.values()))
    assert t.options["source"] == "/v0.mp4" and t.options["pause"] is True
    assert (run.videos[0]["state"], run.videos[0]["task"]) == ("running", t.id)


def test_phase_2_redoes_the_whole_video_without_pause(tmp_path, monkeypatch):
    g = manager(tmp_path, monkeypatch)
    run = a_project(g, tmp_path, ["done", "error"], phase=2)
    g.projects.retry(run.id)
    g.projects.feed()
    t = next(iter(g.queue.tasks.values()))
    assert t.options["source"] == "/v1.mp4" and t.options["pause"] is False


def test_phase_2_translates_the_ready_videos_first(tmp_path, monkeypatch):
    g = manager(tmp_path, monkeypatch)
    run = a_project(g, tmp_path, ["todo", "prepared"], phase=2)
    assert run.next_item() is run.items()[1]
