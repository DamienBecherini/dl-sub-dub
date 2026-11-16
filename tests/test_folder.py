from pathlib import Path

import pytest

from subtitles import folder, source
from helpers import Run
from subtitles.project import store
from subtitles.web.runs import ProjectRun


def test_subtitle_language_code():
    f = folder.subtitle_language_code
    assert f("Film", Path("Film.fr.srt")) == "fr"
    assert f("Film", Path("Film.pt-BR.vtt")) == "pt"
    assert f("Film", Path("Film.eng.srt")) == "eng"
    assert f("Film", Path("Film.srt")) == "?"
    assert f("Film", Path("Film 2.fr.srt")) is None
    assert f("Film", Path("Film.fr.txt")) is None
    assert f("Film", Path("Film.forced.srt")) is None


def test_inventory(tmp_path):
    (tmp_path / "S01").mkdir()
    (tmp_path / "S01" / "E01.mp4").write_bytes(b"x")
    (tmp_path / "S01" / "E01.en.srt").write_text("1", encoding="utf-8")
    (tmp_path / "S01" / "E01.fr.srt").write_text("1", encoding="utf-8")
    (tmp_path / "S01" / "E02.mkv").write_bytes(b"xx")
    (tmp_path / "Film.mp4").write_bytes(b"xxx")
    (tmp_path / "notes.txt").write_text("", encoding="utf-8")
    (tmp_path / ".work" / "x").mkdir(parents=True)
    (tmp_path / ".work" / "x" / "ignore.mp4").write_bytes(b"x")
    inv = folder.inventory(tmp_path, with_track_language=False)
    assert inv["count"] == 3
    names = [(v["folder"], v["name"], v["subtitles"]) for v in inv["videos"]]
    assert names == [("", "Film.mp4", []), ("S01", "E01.mp4", ["en", "fr"]), ("S01", "E02.mkv", [])]
    assert folder.inventory(tmp_path, recursive=False, with_track_language=False)["count"] == 1
    with pytest.raises(source.SourceError):
        folder.inventory(tmp_path / "absent")


def test_project_file(tmp_path):
    assert store.path_for(Path("D:/src"), Path("D:/out"), True) == Path("D:/out") / store.FILE_NAME
    assert store.path_for(Path("D:/src"), Path("D:/out"), False) == Path("D:/src") / store.FILE_NAME
    f = tmp_path / store.FILE_NAME
    videos = [{"path": "D:/a.mp4", "name": "a.mp4", "language": "en", "state": "done", "task": "t1", "count": 10, "detected_language": "en", "error": ""},
              {"path": "D:/b.mp4", "name": "b.mp4", "language": "", "state": "prepared", "task": "t2", "count": 5, "detected_language": "en", "error": ""},
              {"path": "D:/c.mp4", "name": "c.mp4", "language": "", "state": "error", "task": "t3", "count": None, "detected_language": "", "error": "x"}]
    run = Run("D:/", videos, {"languages": ["fr"], "pause": True}, f)
    run.phase, run.state = 2, "running"
    run.save()
    assert f.exists()
    reread = ProjectRun.from_file(f)
    assert reread.id == run.id and reread.state == "suspended" and reread.phase == 1 and reread.options == {"languages": ["fr"], "pause": True}
    assert [v["state"] for v in reread.videos] == ["done", "todo", "todo"]
    assert reread.videos[1]["task"] is None and reread.videos[0]["task"] == "t1"
    assert reread.next_item().video == "D:/b.mp4"
    r = reread.summary()
    assert r["counts"]["done"] == 1 and r["counts"]["todo"] == 2 and r["total"] == 3
    with pytest.raises(source.SourceError):
        store.load(tmp_path / "absent.json")
