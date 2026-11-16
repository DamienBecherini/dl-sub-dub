import json
import threading
from pathlib import Path

from subtitles.web import last_project
from subtitles import config, pipeline
from subtitles.web import tasks
from helpers import Run, update
from subtitles.web.manager import Manager
from subtitles.web.tasks import TaskQueue


def manager(tmp_path, monkeypatch):
    """Manager without its execution thread or the real history: nothing really starts."""
    monkeypatch.setattr(last_project, "FILE", tmp_path / "last_project.json")
    g = Manager(TaskQueue(history=[]), start=False)
    return g


def a_project(g, tmp_path, state="suspended"):
    videos = [{"path": "D:/a.mp4", "name": "a.mp4", "language": "en", "state": "done", "task": "t1", "count": 1, "detected_language": "en", "error": ""},
              {"path": "D:/b.mp4", "name": "b.mp4", "language": "", "state": "todo", "task": None, "count": None, "detected_language": "", "error": ""}]
    run = Run("D:/", videos, {"translator": "local", "languages": ["fr"], "pause": False}, tmp_path / "subtitles.batch.json")
    run.state = state
    run.after_save = last_project.remember
    g.projects.all[run.id] = run
    run.save()
    return run


def test_resume_applies_the_new_translator_to_the_next_videos(tmp_path, monkeypatch):
    g = manager(tmp_path, monkeypatch)
    run = a_project(g, tmp_path)
    assert g.projects.resume(run.id, {"translator": "claude", "languages": ["fr", "de"], "pause": True})
    g.projects.feed()
    t = g.queue.tasks[run.videos[1]["task"]]
    assert t.options["translator"] == "claude" and t.options["languages"] == ["fr", "de"] and t.options["pause"] is True


def test_resume_without_changes_keeps_the_settings(tmp_path, monkeypatch):
    g = manager(tmp_path, monkeypatch)
    run = a_project(g, tmp_path)
    assert g.projects.resume(run.id, {})
    assert run.options == {"translator": "local", "languages": ["fr"], "pause": False}


def test_last_project_offered_at_the_next_session(tmp_path, monkeypatch):
    g = manager(tmp_path, monkeypatch)
    run = a_project(g, tmp_path)
    assert last_project.offered(g.projects.all) is None                     # already open in this session
    following = manager(tmp_path, monkeypatch)
    d = last_project.offered(following.projects.all)
    assert d["state"] == "suspended" and d["total"] == 2 and d["done"] == 1 and d["translator"] == "local"
    assert Path(d["file"]) == run.file
    assert d["stages"] == [str(s) for s in run.project.stages] and d["stages"]   # "Resume: Subtitles" says them
    last_project.forget()
    assert last_project.offered(following.projects.all) is None and run.file.exists()


def test_the_last_project_written_under_the_old_name_of_its_file_is_still_offered(tmp_path, monkeypatch):
    g = manager(tmp_path, monkeypatch)
    run = a_project(g, tmp_path)
    new = last_project.FILE
    old = new.with_name(last_project.OLD_NAME)
    new.rename(old)                                           # as a version before wrote it (last_batch.json)
    following = manager(tmp_path, monkeypatch)
    assert Path(last_project.offered(following.projects.all)["file"]) == run.file
    last_project.forget()
    assert last_project.offered(following.projects.all) is None and not old.exists()


def test_finished_project_or_missing_file_not_offered(tmp_path, monkeypatch):
    g = manager(tmp_path, monkeypatch)
    run = a_project(g, tmp_path)
    run.state = "done"
    run.save()
    assert last_project.offered(manager(tmp_path, monkeypatch).projects.all) is None

    run2 = a_project(g, tmp_path)
    run2.file.unlink()
    assert last_project.offered(manager(tmp_path, monkeypatch).projects.all) is None


def prepared(work, count=10):
    work.mkdir(parents=True, exist_ok=True)
    return pipeline.Prepared(work / "v.mp4", work, "v", "en", [object()] * count, work / "v.en.srt", work, 0.0)


def test_remaining_languages(tmp_path):
    p = prepared(tmp_path)
    assert pipeline.remaining_languages(p, ["en", "fr", "de"]) == ["fr", "de"]
    (tmp_path / "translation.de.json").write_text(json.dumps(["x"] * 10), encoding="utf-8")
    (tmp_path / "translation.fr.json").write_text(json.dumps(["x"] * 3), encoding="utf-8")   # other segmentation: to redo
    assert pipeline.remaining_languages(p, ["fr", "de"]) == ["fr"]
    assert pipeline.remaining_languages(p, ["fr", "de"], redo=True) == ["fr", "de"]


def test_cumulated_estimate_of_the_project_without_the_translations_already_done(tmp_path, monkeypatch):
    g = manager(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "load", lambda: {"translation": {"chunk_size": 25}, "translators": [{"id": "claude", "type": "claude", "model": "claude-sonnet-5-5"}]})
    run = a_project(g, tmp_path, state="awaiting_translation")
    for i, v in enumerate(run.videos):
        t = tasks.Task({"source": v["path"], "project": run.id})
        t.prepared = prepared(tmp_path / f"v{i}")
        g.queue.tasks[t.id] = t
        update(run, i, state="prepared", task=t.id)
    e = g.projects.estimate(run.id, ["fr"])
    assert e["videos"] == 2 and e["count"] == 20 and e["requests"] == 2 and e["input_tokens"] == 20 * 36
    (tmp_path / "v0" / "translation.fr.json").write_text(json.dumps(["x"] * 10), encoding="utf-8")
    e = g.projects.estimate(run.id, ["fr"])
    assert e["videos"] == 1 and e["count"] == 10
    (tmp_path / "v1" / "translation.fr.json").write_text(json.dumps(["x"] * 10), encoding="utf-8")
    assert g.projects.estimate(run.id, ["fr"])["languages"] == []


def test_last_video_translated_alone_ends_the_project(tmp_path, monkeypatch):
    g = manager(tmp_path, monkeypatch)
    run = a_project(g, tmp_path, state="awaiting_translation")
    t = tasks.Task({"source": "D:/b.mp4", "project": run.id})
    t.state = "done"
    update(run, 1, state="prepared", task=t.id)
    g.projects.task_changed(t)
    assert run.state == "done" and run.videos[1]["state"] == "done"
    assert last_project.offered(manager(tmp_path, monkeypatch).projects.all) is None


def test_fully_done_project_not_offered_even_if_its_state_stayed_open(tmp_path, monkeypatch):
    g = manager(tmp_path, monkeypatch)
    run = a_project(g, tmp_path, state="awaiting_translation")
    update(run, 1, state="done")
    run.save()
    assert last_project.offered(manager(tmp_path, monkeypatch).projects.all) is None


def test_loading_a_project_already_open_does_not_block(tmp_path, monkeypatch):
    g = manager(tmp_path, monkeypatch)
    run = a_project(g, tmp_path, state="suspended")
    result = []
    thread = threading.Thread(target=lambda: result.append(g.projects.load(str(run.file))), daemon=True)
    thread.start()
    thread.join(5)
    assert result and result[0].state == "suspended"
    assert last_project.offered(manager(tmp_path, monkeypatch).projects.all) is not None     # the reloaded project stays offered
