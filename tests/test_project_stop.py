""""Stop" a project: paused at once, the running videos interrupted and put back in the list, nothing lost."""
from subtitles.web import observed
from subtitles import config
from subtitles.project import Stage, StageState
from subtitles.states import RunState, TaskState, VideoState
from helpers import Run, update
from subtitles.web.manager import Manager
from subtitles.web.tasks import TaskQueue


def setup(tmp_path, monkeypatch, phase=1, task_state=TaskState.RUNNING, pass_no=1):
    monkeypatch.setattr(config, "load", lambda: {"output_dir": str(tmp_path), "translators": [], "active_translator": ""})
    g = Manager(TaskQueue(history=[]), start=False)
    videos = [{"path": f"/v{i}.mp4", "name": f"v{i}.mp4", "language": "", "state": VideoState.TODO, "task": None, "error": ""} for i in range(3)]
    run = Run("/r", videos, {"pause": False}, tmp_path / "subtitles.batch.json", "L")
    run.phase = phase
    g.projects.all["L"] = run
    t = g.queue.add({"source": "/v0.mp4", "project": "L"})
    t.state, t.pass_no = task_state, pass_no
    update(run, 0, state="dubbing" if pass_no == 3 else "running", task=t.id)
    update(run, 1, state="done")
    return g, run, t


def test_stop_pauses_and_interrupts_the_running_video(tmp_path, monkeypatch):
    g, run, t = setup(tmp_path, monkeypatch)
    assert g.projects.stop("L")
    assert run.state == RunState.SUSPENDED and t.cancel_requested
    t.state = TaskState.CANCELLED                 # what the task does at the end of its current chunk
    g.projects.task_changed(t)
    v = run.videos[0]
    assert v["state"] == VideoState.TODO and v["task"] is None and v["error"] == ""
    assert run.videos[1]["state"] == VideoState.DONE and run.state == RunState.SUSPENDED


def test_a_video_stopped_while_dubbing_waits_for_the_dubbing_again(tmp_path, monkeypatch):
    g, run, t = setup(tmp_path, monkeypatch, phase=3, pass_no=3)
    g.projects.stop("L")
    t.state = TaskState.CANCELLED
    g.projects.task_changed(t)
    item = run.items()[0]                       # subtitles done, the dubbing to do again
    assert (item.state(Stage.SUBTITLES), item.state(Stage.DUBBING)) == (StageState.DONE, StageState.TODO)


def test_a_waiting_task_stops_at_once(tmp_path, monkeypatch):
    g, run, t = setup(tmp_path, monkeypatch, task_state=TaskState.QUEUED)
    g.projects.stop("L")
    assert t.state == TaskState.CANCELLED and run.videos[0]["state"] == VideoState.TODO


def test_resume_starts_the_stopped_video_again(tmp_path, monkeypatch):
    g, run, t = setup(tmp_path, monkeypatch, task_state=TaskState.QUEUED)
    g.projects.stop("L")
    assert g.projects.resume("L")
    g.projects.feed()
    v = run.videos[0]
    assert v["state"] == VideoState.RUNNING and v["task"] != t.id


def test_cancel_still_cancels(tmp_path, monkeypatch):
    g, run, t = setup(tmp_path, monkeypatch)
    g.projects.cancel("L")
    t.state = TaskState.CANCELLED
    g.projects.task_changed(t)
    assert run.videos[0]["state"] == VideoState.CANCELLED and run.state == RunState.CANCELLED


def test_a_project_paused_after_its_videos_can_still_be_stopped_now(tmp_path, monkeypatch):
    """Pause lets the running video finish: Stop now interrupts it all the same."""
    g, run, t = setup(tmp_path, monkeypatch)
    assert g.projects.suspend("L") and not t.cancel_requested
    assert g.projects.stop("L") and t.cancel_requested and run.state == RunState.SUSPENDED
    run.state = RunState.DONE
    assert not g.projects.stop("L") and not g.projects.stop("unknown")


def test_a_project_stopped_stays_started(tmp_path, monkeypatch):
    """Its videos back to do, it is still a project that ran: paused, resumed rather than run, no longer moved."""
    g, run, t = setup(tmp_path, monkeypatch)
    run.project.ran = True                      # what _add_task wrote when the task started
    g.projects.stop("L")
    t.state = TaskState.CANCELLED
    g.projects.task_changed(t)
    assert run.started() and run.summary()["started"]


def test_where_the_task_of_a_video_stands(tmp_path, monkeypatch):
    g, run, t = setup(tmp_path, monkeypatch)
    t.step, t.progress = "translation", {"done": 45, "total": 120, "more": 0, "step": "translation"}
    assert g.projects._task_work(t.id) == {"step": "translation", "progress": {"done": 45, "total": 120},
                                          "remaining_s": None, "queued": False}
    t.state = TaskState.DONE
    assert g.projects._task_work(t.id) is None


def test_a_translation_stopped_on_request_is_cancelled_not_in_error(tmp_path, monkeypatch):
    """The translator stops with its own error when asked to: the task is then cancelled, not in error."""
    from subtitles import pipeline, translation
    q = TaskQueue(history=[])
    t = q.add({"source": str(tmp_path / "v.mp4")})
    from types import SimpleNamespace
    t.prepared = SimpleNamespace(video=tmp_path / "v.mp4", code="en", cues=[1], source_srt=tmp_path / "v.en.srt", work=tmp_path)
    monkeypatch.setattr(config, "load", lambda: {"output_dir": str(tmp_path)})

    def interrupted(*_a, **_k):
        t.cancel_requested = True
        raise translation.TranslationError("annulé")
    monkeypatch.setattr(pipeline, "translate_and_copy", interrupted)
    monkeypatch.setattr(q, "request", lambda *_a: pipeline.Request("v.mp4", {}))
    q._run(t)
    assert t.state == TaskState.CANCELLED and t.error == ""


def test_resume_on_another_transcription_moves_the_video_still_transcribing(tmp_path, monkeypatch):
    """Pause lets the running video go on: resumed with another transcription engine, it starts again on that one."""
    g, run, t = setup(tmp_path, monkeypatch)
    t.options["transcription"] = "server"
    assert g.projects.suspend("L")
    assert g.projects.resume("L", {"transcription": "local"})
    assert t.cancel_requested and t.stopped
    t.state = TaskState.CANCELLED
    g.projects.task_changed(t)
    g.projects.feed()
    v = run.videos[0]
    assert v["state"] == VideoState.RUNNING and g.queue.get(v["task"]).options["transcription"] == "local"


def test_resume_keeps_a_video_already_transcribed_or_on_the_same_engine(tmp_path, monkeypatch):
    g, run, t = setup(tmp_path, monkeypatch)
    t.options["transcription"] = "local"
    g.projects.suspend("L")
    g.projects.resume("L", {"transcription": "local"})
    assert not t.cancel_requested
    t.options["transcription"], t.prepared = "server", object()     # transcription done: nothing to move
    g.projects.suspend("L")
    g.projects.resume("L", {"transcription": "local"})
    assert not t.cancel_requested


def test_a_cancelled_project_is_resumed_from_its_file(tmp_path, monkeypatch):
    """Resume on a cancelled project reads its file back: finished videos kept, the cancelled one to do again."""
    monkeypatch.setattr(observed, "measure_durations", lambda *_a: None)
    monkeypatch.setattr(observed, "observe", lambda *_a: None)
    g, run, t = setup(tmp_path, monkeypatch)
    assert g.projects.cancel("L")
    assert run.videos[0]["state"] == VideoState.CANCELLED
    again = g.projects.load(str(run.file))
    assert again.state == RunState.SUSPENDED
    assert [v["state"] for v in again.videos] == [VideoState.TODO, VideoState.DONE, VideoState.TODO]
    assert g.projects.resume(again.id)


def test_time_left_of_a_translation_from_its_speed(tmp_path, monkeypatch):
    """During the translation: the lines left (this language and the next) at the speed seen since its first chunk."""
    from subtitles.web import tasks
    g, run, t = setup(tmp_path, monkeypatch)
    t.state, t.pass_started, t.step = TaskState.RUNNING, 1000.0, "translation"
    clock = [2000.0]
    monkeypatch.setattr(tasks.time, "time", lambda: clock[0])
    t.translated(25, 1000, more=1)
    assert t.remaining_s() is None or t.remaining_s() >= 0          # one chunk: not measured yet (the rate of the settings)
    clock[0] = 2020.0
    t.translated(75, 1000, more=1)                                   # 50 lines in 20 s: 0.4 s per line
    assert t.remaining_s() == round((925 + 1000) * 0.4)
    clock[0] = 2030.0
    t.translated(25, 1000, more=0)                                   # the next language: measured again
    assert t._progress_from == (2030.0, 25) and t.progress == {"done": 25, "total": 1000, "more": 0, "step": "translation"}


def test_time_left_of_a_dubbing_and_progress_of_the_current_step_only(tmp_path, monkeypatch):
    """The dubbing reports its sentences: time left from their speed; the translation's progress is not shown under it."""
    from subtitles import dubbing
    from subtitles.web import tasks
    g, run, t = setup(tmp_path, monkeypatch)
    t.state, t.pass_started, t.step = TaskState.RUNNING, 1000.0, "translation"
    clock = [2000.0]
    monkeypatch.setattr(tasks.time, "time", lambda: clock[0])
    t.translated(1034, 1034)
    t.step = "dubbing"
    assert g.projects._task_work(t.id)["progress"] is None                # the dubbing has not said anything yet
    seen = []
    assert dubbing.worker_message('{"key": "log.dub_progress", "done": 25, "n": 682, "s": 40}', lambda d, n: seen.append((d, n)))
    assert seen == [(25, 682)]
    t.translated(25, 682)
    clock[0] = 2040.0
    t.translated(75, 682)                                                # 50 sentences in 40 s
    w = g.projects._task_work(t.id)
    assert w["progress"] == {"done": 75, "total": 682} and w["remaining_s"] == round((682 - 75) * 0.8)


def test_time_left_before_the_first_answer_and_while_mixing(tmp_path, monkeypatch):
    """Translation not answered yet: the estimate made before it, counted down; the dubbed voice being put together:
    about 3 s per minute of video. Past the planned time: unknown (not 0)."""
    from subtitles import config
    from subtitles.web import tasks
    g, run, t = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "load", lambda *_a, **_k: {"active_translator": "local"})
    t.state, t.pass_started = TaskState.RUNNING, 1000.0
    t.step, t.step_started = "translation", 2000.0
    t.estimate = {"translators": [{"id": "claude", "duration_s": 30}, {"id": "local", "duration_s": 142}]}
    assert t.remaining_s(now=2042.0) == 100
    assert t.remaining_s(now=2200.0) is None
    t.step, t.step_started, t.video_duration = "dub_mix", 3000.0, 2500.0
    assert t.remaining_s(now=3015.0) == round(2500 * tasks.MIX_PER_SECOND - 15)


def test_several_voices_the_time_left_counts_those_still_to_dub(tmp_path, monkeypatch):
    """Three languages dubbed one after the other: while the first voice is put together with the background, the
    time left is not the few seconds of that mixing, the ring stays where the sentences are, and the page is told
    which voice it is."""
    from subtitles.web import tasks
    g, run, t = setup(tmp_path, monkeypatch)
    t.state, t.pass_started, t.video_duration = TaskState.RUNNING, 1000.0, 2500.0
    mix = 2500 * tasks.MIX_PER_SECOND
    clock = [2000.0]
    monkeypatch.setattr(tasks.time, "time", lambda: clock[0])
    t.step = "dubbing"
    t.translated(0, 3 * 700, 0, [1, 3])
    clock[0] = 2700.0
    t.translated(700, 3 * 700, 0, [1, 3])                                # the first voice: 700 sentences in 700 s
    assert t.remaining_s() == round(1400 * 1.0 + 3 * mix)                # two voices left, and three mixings
    t.step, t.step_started = "dub_mix", 2700.0
    clock[0] = 2710.0
    w = g.projects._task_work(t.id)
    assert w["progress"] == {"done": 700, "total": 2100} and w["voice"] == [1, 3]      # a third done, not back to nothing
    assert w["remaining_s"] == round((mix - 10) + 1400 * 1.0 + 2 * mix)
    assert t.remaining_s(now=2700.0 + mix + 30) == round(1400 * 1.0 + 2 * mix)         # the mixing takes longer than planned
    t.step = "dubbing"
    clock[0] = 2800.0
    t.translated(700 + 10, 3 * 700, 0, [2, 3])                           # the second voice: its speed is measured again
    assert t._progress_from == (2800.0, 710) and g.projects._task_work(t.id)["voice"] == [2, 3]
    clock[0] = 2900.0
    t.translated(700 + 60, 3 * 700, 0, [2, 3])                           # 50 sentences in 100 s
    assert t.remaining_s() == round((2100 - 760) * 2.0 + 2 * mix)
    t.translated(2100, 2100, 0, [3, 3])
    t.step, t.step_started = "dub_mix", 2900.0                           # the last voice: only its mixing is left
    assert t.remaining_s(now=2915.0) == round(mix - 15) and t.remaining_s(now=2900.0 + mix + 5) is None

