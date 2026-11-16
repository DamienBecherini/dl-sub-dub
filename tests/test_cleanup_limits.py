"""The limits of the whole working folder (the oldest videos' folders go), the dubbed voices as a category, and the
audio of a task cancelled or stopped."""
import os
import time

from subtitles import cleanup, config
from subtitles.states import TaskState
from subtitles.web.tasks import Task, TaskQueue


def folder(root, name, size, days_old, now):
    d = root / name
    (d / "dubbing").mkdir(parents=True)
    f = d / "transcription.json"
    f.write_bytes(b"x" * size)
    t = now - days_old * 86400
    for p in (f, d / "dubbing", d):
        os.utime(p, (t, t))
    return d


def test_the_oldest_folders_go_by_age_then_by_size(tmp_path):
    now = time.time()
    a = folder(tmp_path, "a", 400_000, 40, now)
    b = folder(tmp_path, "b", 400_000, 10, now)
    c = folder(tmp_path, "c", 400_000, 5, now)
    fresh = folder(tmp_path, "fresh", 900_000, 0, now)                      # a task at work: never removed
    assert cleanup.limit_work(tmp_path, {"max_age_days": 0, "max_size_mb": 0}, now) == {"files": 0, "bytes": 0}
    gone = cleanup.limit_work(tmp_path, {"max_age_days": 30, "max_size_mb": 1.5}, now)
    assert not a.exists()                                                    # older than 30 days
    assert not b.exists() and c.exists() and fresh.exists()                  # then the oldest while over 1.5 MB
    assert gone == {"files": 2, "bytes": 800_000}


def test_the_dubbed_voices_are_a_category(tmp_path):
    (tmp_path / "dubbing").mkdir()
    (tmp_path / "dubbing" / "fr.m4a").write_bytes(b"voice")
    assert [p.name for p in cleanup.classify(tmp_path)["dubbing"]] == ["fr.m4a"]
    assert cleanup.clean_folder(tmp_path, cleanup.DEFAULTS) == (0, 0)       # kept by default
    assert cleanup.clean_folder(tmp_path, {**cleanup.DEFAULTS, "dubbing": True}) == (1, 5)


def test_a_cancelled_task_leaves_no_audio(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "load", lambda: {"output_dir": str(tmp_path), "cleanup": {"audio": True}})
    t = Task({"source": "v.mp4"})
    t.work = tmp_path
    (tmp_path / "v.audio.opus").write_bytes(b"a" * 10)
    (tmp_path / "transcription.json").write_text("{}")
    t.state = TaskState.CANCELLED
    TaskQueue._clean_after_cancel(t)
    assert not (tmp_path / "v.audio.opus").exists() and (tmp_path / "transcription.json").exists()
