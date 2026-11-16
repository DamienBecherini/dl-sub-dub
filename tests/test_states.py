"""States as StrEnum, and the task queue without a orchestrator."""
import json

from subtitles.project import store
from subtitles.states import RunState, TaskState, VideoState
from subtitles.web.orchestrator import Orchestrator
from helpers import Run
from subtitles.web.runs import ProjectRun
from subtitles.web.manager import Manager
from subtitles.web.tasks import NoEvents, TaskQueue


def test_states_are_plain_text_in_json():
    q = TaskQueue(history=[])
    t = q.add({"source": "v.mp4"})
    d = json.loads(json.dumps(t.summary()))
    assert d["state"] == "queued" and t.state == TaskState.QUEUED == "queued"


def test_a_project_file_read_back_gives_the_same_states(tmp_path):
    file = tmp_path / store.FILE_NAME
    videos = [{"path": "a.mp4", "name": "a.mp4", "state": VideoState.DONE, "task": "t1"},
              {"path": "b.mp4", "name": "b.mp4", "state": VideoState.PREPARED, "task": "t2"}]
    Run("r", videos, {}, file).save()
    assert json.loads(file.read_text(encoding="utf-8"))["items"][0]["stages"]["subtitles"]["state"] == "done"
    back = ProjectRun.from_file(file)
    assert [v["state"] for v in back.videos] == [VideoState.DONE, VideoState.TODO]
    assert back.state == RunState.SUSPENDED and isinstance(back.videos[0]["state"], VideoState)


def test_the_queue_works_alone():
    q = TaskQueue(history=[])
    assert isinstance(q.events, NoEvents)
    t = q.add({"source": "v.mp4", "project": "unknown"})
    assert q.cancel(t.id) and t.state == TaskState.CANCELLED


def test_the_project_manager_listens_to_the_queue():
    g = Manager(TaskQueue(history=[]), start=False)
    assert g.queue.events is g.projects and isinstance(g.projects, Orchestrator)
    assert not g.busy() and g.activity() == "" and g.remaining_s() is None
