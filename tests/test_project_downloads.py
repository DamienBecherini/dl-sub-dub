"""Addresses in a project: downloaded two at a time while the videos already there are transcribed, with the quality
chosen for each one; stopped, cancelled or failed like any step; an old batch file opens as a project."""
import json
from pathlib import Path

import pytest

from subtitles.web import observed
from subtitles import config, source
from subtitles.project import Stage, StageState, store
from subtitles.web import downloads
from subtitles.web.manager import Manager
from subtitles.web.tasks import TaskQueue
from helpers import titled

S = StageState
A, B, C = "https://x.example/a", "https://x.example/b", "https://x.example/c"


@pytest.fixture
def g(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "load", lambda: {"output_dir": str(tmp_path / "out"), "youtube": {"command": ["yt-dlp"], "max_height": 1080}})
    monkeypatch.setattr(downloads.DownloadJob, "start", lambda self: None)       # run by hand below
    g = Manager(TaskQueue(history=[]), start=False)
    monkeypatch.setattr(observed, "measure_durations", lambda *_a: None)
    return g


def fake_download(tmp_path, monkeypatch, fail=(), seen=None):
    def download(url, command, log, folder, name, cancelled, extra=(), on_line=None, keep_partial=lambda: False):
        if seen is not None:
            seen.append((url, command, name))
        if url in fail:
            raise source.SourceError("HTTP Error 403")
        f = Path(folder) / (titled(name, url[-1]) + ".mp4")
        part = f.with_name(f.name + ".part")
        if cancelled():
            stopped = source.DownloadCancelled("cancelled")
            if keep_partial():                                  # a pause: what was downloaded stays
                part.write_bytes(b"half")
                stopped.partial = f
            raise stopped
        part.unlink(missing_ok=True)
        f.write_bytes(b"video")
        return f
    monkeypatch.setattr(source, "download_youtube", download)


def project(g, tmp_path, sources, **options):
    folder = tmp_path / "list"
    folder.mkdir(exist_ok=True)
    return g.projects.create(folder.as_posix(), [{"source": s} for s in sources], {"pause": False, **options}, title="List")


def finish(g, run):
    """Ends the downloads started (their threads are run by hand)."""
    for job in list(g.projects.fetching.downloads.values()):
        job.run()


def test_two_downloads_at_once_while_the_files_are_transcribed(g, tmp_path, monkeypatch):
    seen = []
    fake_download(tmp_path, monkeypatch, seen=seen)
    video = tmp_path / "list" / "local.mp4"
    (tmp_path / "list").mkdir()
    video.write_bytes(b"x")
    run = project(g, tmp_path, [A, B, video.as_posix(), C])
    assert run.project.stages == [Stage.DOWNLOAD, Stage.SUBTITLES]
    g.projects.feed()
    states = [(i.state(Stage.DOWNLOAD), i.state(Stage.SUBTITLES)) for i in run.project.items]
    assert states == [(S.RUNNING, S.TODO), (S.RUNNING, S.TODO), (S.SKIPPED, S.RUNNING), (S.TODO, S.TODO)]
    assert [n for _u, _c, n in seen] == []                                         # nothing ran yet: started by hand
    finish(g, run)
    a = run.project.items[0]
    assert a.state(Stage.DOWNLOAD) == S.DONE and a.video.endswith("/list/001 - a.mp4") and a.title == "001 - a"
    g.projects.feed()                                                              # the third address starts
    assert run.project.items[3].state(Stage.DOWNLOAD) == S.RUNNING
    assert json.loads(run.file.read_text(encoding="utf-8"))["items"][0]["video"] == a.video


def test_the_quality_chosen_for_one_video(g, tmp_path, monkeypatch):
    seen = []
    fake_download(tmp_path, monkeypatch, seen=seen)
    run = project(g, tmp_path, [A, B])
    run.project.items[1].overrides["max_height"] = 480
    g.projects.feed()
    finish(g, run)
    commands = {url: " ".join(c) for url, c, _n in seen}
    assert "height<=1080" in commands[A] and "height<=480" in commands[B]


def test_phase_1_waits_for_the_downloads(g, tmp_path, monkeypatch):
    fake_download(tmp_path, monkeypatch)
    run = project(g, tmp_path, [A])
    g.projects.feed()
    run.end_phase()
    assert run.state == "running"                     # the video still to download must be transcribed too
    finish(g, run)
    g.projects.feed()
    assert run.project.items[0].state(Stage.SUBTITLES) == S.RUNNING


def test_stop_puts_the_download_back_cancel_cancels_it(g, tmp_path, monkeypatch):
    fake_download(tmp_path, monkeypatch)
    run = project(g, tmp_path, [A, B])
    g.projects.feed()
    assert g.projects.stop(run.id)
    finish(g, run)
    assert [i.state(Stage.DOWNLOAD) for i in run.project.items] == [S.TODO, S.TODO]
    assert g.projects.resume(run.id)
    g.projects.feed()
    assert g.projects.cancel(run.id)
    finish(g, run)
    assert [i.state(Stage.DOWNLOAD) for i in run.project.items] == [S.CANCELLED, S.CANCELLED]
    assert [v["state"] for v in run.videos] == ["cancelled", "cancelled"]


def test_a_paused_download_keeps_its_partial_files_and_resumes(g, tmp_path, monkeypatch):
    fake_download(tmp_path, monkeypatch)
    run = project(g, tmp_path, [A, B])
    a, b = run.project.items
    g.projects.feed()
    assert g.projects.stop(run.id)                                   # the pause of the project
    finish(g, run)
    parts = sorted(p.name for p in (tmp_path / "list").glob("*.part"))
    assert parts == ["001 - a.mp4.part", "002 - b.mp4.part"]
    assert a.facts["partial"].endswith("/list/001 - a.mp4") and a.state(Stage.DOWNLOAD) == S.TODO
    assert run.summary()["items"][0]["facts"]["partial"]             # the page says it will resume
    assert g.projects.remove_items(run.id, [b.id]) == 1              # its line removed: nothing of it is left
    assert sorted(p.name for p in (tmp_path / "list").glob("*.part")) == ["001 - a.mp4.part"]
    assert g.projects.resume(run.id)
    g.projects.feed()
    finish(g, run)
    assert a.state(Stage.DOWNLOAD) == S.DONE and "partial" not in a.facts
    assert sorted(p.name for p in (tmp_path / "list").glob("001*")) == ["001 - a.mp4"]


def test_a_paused_download_of_a_cancelled_project_leaves_nothing(g, tmp_path, monkeypatch):
    fake_download(tmp_path, monkeypatch)
    run = project(g, tmp_path, [A])
    g.projects.feed()
    g.projects.stop(run.id)
    finish(g, run)
    assert list((tmp_path / "list").glob("*.part"))
    assert g.projects.cancel(run.id)
    assert not list((tmp_path / "list").glob("*.part")) and "partial" not in run.project.items[0].facts


def test_partial_files_named_by_a_project_file_are_deleted_only_in_its_folder(g, tmp_path, monkeypatch):
    fake_download(tmp_path, monkeypatch)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    for n in ("photo.jpg", "photo.mp4.part"):
        (elsewhere / n).write_bytes(b"x")
    run = project(g, tmp_path, [A])
    run.project.items[0].facts["partial"] = (elsewhere / "photo.mp4").as_posix()      # as a project file from elsewhere could say
    assert g.projects.remove_items(run.id, [run.project.items[0].id]) == 1
    assert sorted(p.name for p in elsewhere.iterdir()) == ["photo.jpg", "photo.mp4.part"]


def test_a_track_that_is_no_language_is_an_error_of_the_line(g, tmp_path, monkeypatch):
    fake_download(tmp_path, monkeypatch)
    run = project(g, tmp_path, [A])
    a = run.project.items[0]
    a.overrides["audio"] = ["../../x"]                                                # as a project file from elsewhere could say
    g.projects.feed()
    assert a.state(Stage.DOWNLOAD) == S.ERROR and "../../x" in a.stages[Stage.DOWNLOAD].error and not g.projects.fetching.downloads


def test_a_folder_too_deep_for_a_file_name_is_an_error_of_the_line(g, tmp_path, monkeypatch):
    fake_download(tmp_path, monkeypatch)

    def refused(folder=None, prefix=""):
        raise source.SourceError("folder too long")
    monkeypatch.setattr(source, "name_template", refused)
    run = project(g, tmp_path, [A])
    g.projects.feed()
    a = run.project.items[0]
    assert a.state(Stage.DOWNLOAD) == S.ERROR and a.stages[Stage.DOWNLOAD].error == "folder too long" and not g.projects.fetching.downloads


def test_a_failed_download_is_an_error_to_retry(g, tmp_path, monkeypatch):
    fake_download(tmp_path, monkeypatch, fail={B})
    run = project(g, tmp_path, [A, B])
    g.projects.feed()
    finish(g, run)
    b = run.project.items[1]
    assert b.state(Stage.DOWNLOAD) == S.ERROR and run.videos[1]["error"] == "HTTP Error 403"
    assert g.projects.retry(run.id, B)
    assert b.state(Stage.DOWNLOAD) == S.TODO


def test_an_address_already_downloaded_is_not_downloaded_again(g, tmp_path, monkeypatch):
    fake_download(tmp_path, monkeypatch)
    run = project(g, tmp_path, [A])
    g.projects.feed()
    finish(g, run)
    again = g.projects.load(str(run.file))
    assert again.project.items[0].state(Stage.DOWNLOAD) == S.DONE


def test_an_old_project_file_opens_as_a_project_next_to_it(g, tmp_path):
    old = tmp_path / store.OLD_FILE_NAME
    old.write_text(json.dumps({"id": "b1", "root": tmp_path.as_posix(), "options": {"languages": ["fr"]}, "state": "suspended",
                               "videos": [{"path": "D:/v/1.mp4", "name": "1.mp4", "state": "done"}]}))
    run = g.projects.load(str(old))
    assert run.file == tmp_path / store.FILE_NAME and run.file.is_file() and old.is_file()
    assert run.id == "b1" and run.videos[0]["state"] == "done"
