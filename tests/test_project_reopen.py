"""A project opened again from its file: suspended when something is left to do, finished when everything ticked is
done (it used to show "paused", with a Resume that had nothing to do)."""
from helpers import at
from subtitles import config
from subtitles.project import Stage, StageState
from subtitles.states import RunState
from subtitles.web.runs import ProjectRun
from subtitles.web.manager import Manager
from subtitles.web.tasks import TaskQueue


def test_opened_again_done_or_suspended(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "load", lambda *_a, **_k: {"output_dir": str(tmp_path / "out")})
    g = Manager(TaskQueue(history=[]), start=False)
    videos = []
    for name in ("a.mp4", "b.mp4"):
        (tmp_path / name).write_bytes(b"x")
        videos.append({"source": (tmp_path / name).as_posix()})
    run = g.projects.create(tmp_path.as_posix(), videos, {}, "Mine", [Stage.SUBTITLES], start=False)
    a, b = run.project.items
    at(a, Stage.SUBTITLES, StageState.DONE)
    run.save()
    assert ProjectRun.from_file(run.file).state == RunState.SUSPENDED          # b is left to do
    at(b, Stage.SUBTITLES, StageState.DONE)
    run.save()
    assert ProjectRun.from_file(run.file).state == RunState.DONE
    at(b, Stage.SUBTITLES, StageState.ERROR, error="x")
    run.save()
    assert ProjectRun.from_file(run.file).state == RunState.SUSPENDED          # an error is tried again
