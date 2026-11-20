"""Translation of a project by a remote API: several videos at once, never more than allowed."""
import threading
from types import SimpleNamespace

from subtitles.web import last_project
from subtitles import config, translation
from helpers import Run, update
from subtitles.web.manager import Manager
from subtitles.web.tasks import Task, TaskQueue

CLAUDE = {"id": "claude", "name": "Claude", "type": "claude"}
LOCAL = {"id": "local", "name": "Local", "type": "openai"}


def manager(monkeypatch, tmp_path, translator):
    monkeypatch.setattr(last_project, "FILE", tmp_path / "last_project.json")
    monkeypatch.setattr(config, "load", lambda: {"translators": [CLAUDE, LOCAL, {**CLAUDE, "id": "two", "parallel": 2}],
                                                 "active_translator": translator, "output_dir": str(tmp_path)})
    g = Manager(TaskQueue(history=[]), start=False)
    return g


def project_in_phase_2(g, tmp_path, count, translator):
    videos = []
    for i in range(count):
        t = Task({"source": f"v{i}.mp4", "project": "L"})
        t.state, t.prepared = "paused", SimpleNamespace(cues=[1] * 100, code="en")
        g.queue.tasks[t.id] = t
        g.queue.order.append(t.id)
        videos.append({"path": f"v{i}.mp4", "name": f"v{i}.mp4", "state": "prepared", "task": t.id, "count": 100, "error": ""})
    run = Run("r", videos, {"translator": translator}, tmp_path / "project.json", "L")
    run.phase = 2
    g.projects.all["L"] = run
    return run


def test_number_of_concurrent_videos_depends_on_the_translator():
    assert translation.concurrency(CLAUDE) == 5
    assert translation.concurrency(LOCAL) == 1
    assert translation.concurrency({**CLAUDE, "parallel": 3}) == 3
    assert translation.concurrency({**LOCAL, "parallel": "abc"}) == 1
    assert translation.concurrency({**CLAUDE, "parallel": 500}) == translation.MAX_CONCURRENCY


def test_claude_starts_five_videos_and_no_more(monkeypatch, tmp_path):
    g = manager(monkeypatch, tmp_path, "claude")
    run = project_in_phase_2(g, tmp_path, 8, "claude")
    g.projects.feed()
    g.projects.feed()                         # a second pass starts nothing more
    assert sum(v["state"] == "running" for v in run.videos) == 5
    assert sum(g.queue.tasks[v["task"]].state == "queued" for v in run.videos) == 5


def test_the_parallel_setting_of_the_translator_is_respected(monkeypatch, tmp_path):
    g = manager(monkeypatch, tmp_path, "two")
    run = project_in_phase_2(g, tmp_path, 8, "two")
    g.projects.feed()
    assert sum(v["state"] == "running" for v in run.videos) == 2


def test_local_ai_stays_at_one_video(monkeypatch, tmp_path):
    g = manager(monkeypatch, tmp_path, "local")
    run = project_in_phase_2(g, tmp_path, 8, "local")
    g.projects.feed()
    assert sum(v["state"] == "running" for v in run.videos) == 1


def test_transcription_stays_one_at_a_time(monkeypatch, tmp_path):
    g = manager(monkeypatch, tmp_path, "claude")
    run = project_in_phase_2(g, tmp_path, 8, "claude")
    run.phase = 1
    for i in range(len(run.items())):
        update(run, i, state="todo", task=None)
    g.projects.feed()
    assert sum(v["state"] == "running" for v in run.videos) == 1


def test_a_freed_slot_starts_the_next_one(monkeypatch, tmp_path):
    g = manager(monkeypatch, tmp_path, "claude")
    run = project_in_phase_2(g, tmp_path, 8, "claude")
    g.projects.feed()
    first = run.current()
    t = g.queue.tasks[run.view(first)["task"]]
    t.state = "done"
    g.projects.task_changed(t)
    assert run.view(first)["state"] == "done"
    g.projects.feed()
    assert sum(v["state"] == "running" for v in run.videos) == 5
    assert sum(v["state"] == "prepared" for v in run.videos) == 2


def test_the_phase_does_not_end_while_a_video_runs(monkeypatch, tmp_path):
    g = manager(monkeypatch, tmp_path, "claude")
    run = project_in_phase_2(g, tmp_path, 2, "claude")
    g.projects.feed()
    a, b = run.videos
    t = g.queue.tasks[a["task"]]
    t.state = "done"
    g.projects.task_changed(t)
    g.projects.feed()
    assert run.state == "running"
    t = g.queue.tasks[b["task"]]
    t.state = "done"
    g.projects.task_changed(t)
    g.projects.feed()
    assert run.state == "done"


def test_suspended_project_starts_nothing_more(monkeypatch, tmp_path):
    g = manager(monkeypatch, tmp_path, "claude")
    run = project_in_phase_2(g, tmp_path, 8, "claude")
    g.projects.feed()
    g.projects.suspend("L")
    t = g.queue.tasks[run.view(run.current())["task"]]
    t.state = "done"
    g.projects.task_changed(t)
    g.projects.feed()
    assert sum(v["state"] == "running" for v in run.videos) == 4


def test_a_project_paused_while_it_translates_says_paused(monkeypatch, tmp_path):
    """After the go-ahead, the videos transcribed wait for their turn, not for the user: pausing the project then
    shows neither the go-ahead again nor "running" for the videos that end."""
    g = manager(monkeypatch, tmp_path, "claude")
    run = project_in_phase_2(g, tmp_path, 8, "claude")
    run.phase, run.state = 1, "awaiting_translation"
    assert run.summary()["rules"]["go_ahead"]
    assert g.projects.translate("L", "claude", ["fr"]) and len(run.agreed) == 8
    g.projects.feed()
    assert run.summary()["rules"]["status"] == "running"
    g.projects.suspend("L")
    rules = run.summary()["rules"]
    at_work = [v for v in run.videos if v["state"] == "running"]
    assert (rules["status"], rules["go_ahead"], rules["working"]) == ("suspended", False, True) and 0 < len(at_work) == len(run.finishing) < 8
    assert not g.projects.translate("L", "claude", ["fr"])                     # nothing waits for a go-ahead
    for v in at_work:                                                          # they end: still paused, the others wait for their turn
        t = g.queue.tasks[v["task"]]
        t.state = "done"
        g.projects.task_changed(t)
    rules = run.summary()["rules"]
    assert (rules["status"], rules["go_ahead"], rules["working"]) == ("suspended", False, False) and not run.finishing


def test_parallel_translations_run_in_their_own_thread(monkeypatch, tmp_path):
    g = manager(monkeypatch, tmp_path, "claude")
    run = project_in_phase_2(g, tmp_path, 3, "claude")
    g.projects.feed()
    together = threading.Barrier(3, timeout=5)     # fails if the three do not run at the same time
    finished = []

    def run_task(t):
        together.wait()
        finished.append(t.id)

    monkeypatch.setattr(g.queue, "_run", run_task)
    threads_before = set(threading.enumerate())
    for v in run.videos:
        g.queue._start(g.queue.tasks[v["task"]])
    for f in set(threading.enumerate()) - threads_before:
        f.join(6)
    assert len(finished) == 3


def test_time_left_divided_by_the_number_of_concurrent_videos(tmp_path):
    videos = [{"path": f"v{i}", "name": f"v{i}", "state": "prepared", "task": None, "count": 100, "error": ""} for i in range(10)]
    run = Run("r", videos, {"languages": ["fr"]}, tmp_path / "project.json", "L")
    run.phase = 2
    alone = run.remaining_s(known_rate=0.2)
    run.concurrency = 5
    assert alone == 200
    assert run.remaining_s(known_rate=0.2) == 40
