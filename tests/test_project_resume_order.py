"""Resuming a project: the videos already transcribed go first, and do not count in the time left."""

from subtitles.web import last_project
from subtitles.web import observed
from subtitles import config, pipeline
from helpers import Run, update
from subtitles.web.manager import Manager
from subtitles.web.tasks import TaskQueue


def video(name, state="todo", **others):
    return {"path": f"D:/{name}.mp4", "name": f"{name}.mp4", "language": "", "state": state, "task": None, "count": None,
            "detected_language": "", "error": "", "duration": 100, **others}


def project_of(tmp_path, videos, options=None, phase=1):
    run = Run("D:/", videos, options or {}, tmp_path / "subtitles.batch.json")
    run.phase = phase
    return run


def test_videos_already_transcribed_go_first(tmp_path):
    run = project_of(tmp_path, [video("a"), video("b", transcribed=True), video("c", transcribed=True)])
    items = run.items()
    assert run.next_item() is items[1]
    update(run, 1, state="prepared")
    assert run.next_item() is items[2]
    update(run, 2, state="prepared")
    assert run.next_item() is items[0]


def test_without_kept_transcription_the_folder_order_is_kept(tmp_path):
    run = project_of(tmp_path, [video("a"), video("b")])
    assert run.next_item() is run.items()[0]


def test_in_phase_2_the_ready_videos_stay_in_order(tmp_path):
    run = project_of(tmp_path, [video("a", "prepared"), video("b", "prepared", transcribed=True)], phase=2)
    assert run.next_item() is run.items()[0]


def test_finished_project_nothing_more_to_start(tmp_path):
    assert project_of(tmp_path, [video("a", "prepared")]).next_item() is None


def test_time_left_ignores_the_kept_transcriptions(tmp_path):
    run = project_of(tmp_path, [video("a"), video("b", transcribed=True), video("c", transcribed=True)])
    assert run.remaining_s(known_rate=2.0) == 200


def test_detection_from_the_working_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "load", lambda: {"output_dir": str(tmp_path)})
    videos = [video("a"), video("b")]
    work = pipeline.work_dir(tmp_path, videos[1]["path"])
    work.mkdir(parents=True)
    (work / "transcription.json").write_text("{}", encoding="utf-8")
    run = project_of(tmp_path, videos)
    observed.mark_transcribed(run)
    assert [v["transcribed"] for v in run.videos] == [False, True]


def test_redo_transcribes_everything_again(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "load", lambda: {"output_dir": str(tmp_path)})
    videos = [video("a")]
    work = pipeline.work_dir(tmp_path, videos[0]["path"])
    work.mkdir(parents=True)
    (work / "transcription.json").write_text("{}", encoding="utf-8")
    run = project_of(tmp_path, videos, {"redo": True})
    observed.mark_transcribed(run)
    assert run.videos[0]["transcribed"] is False


def test_resume_detects_the_transcriptions(tmp_path, monkeypatch):
    monkeypatch.setattr(last_project, "FILE", tmp_path / "last_project.json")
    monkeypatch.setattr(config, "load", lambda: {"output_dir": str(tmp_path)})
    g = Manager(TaskQueue(history=[]), start=False)
    monkeypatch.setattr(observed, "measure_durations", lambda *_a: None)
    videos = [video("a"), video("b")]
    work = pipeline.work_dir(tmp_path, videos[1]["path"])
    work.mkdir(parents=True)
    (work / "transcription.json").write_text("{}", encoding="utf-8")
    file = tmp_path / "subtitles.batch.json"
    project_of(tmp_path, videos).save()
    resumed = g.projects.load(str(file))
    assert resumed.next_item() is resumed.items()[1]
