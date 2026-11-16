"""Cancelling a download stops yt-dlp at once (not only between two videos of a list) and deletes its partial files;
the videos already there stay, and the playlist download tells how many there are (offered as a project, or resumed)."""
import sys
import threading
import time

import pytest

from subtitles import source
from subtitles.web.playlist import PlaylistDownload
from subtitles.states import DownloadState

# A yt-dlp stand-in: writes the partial files of the video named by -o, then takes its time (killed before the end)
FAKE_YTDLP = """
import sys, time
from pathlib import Path
out = Path(sys.argv[sys.argv.index("-o") + 1].replace("TITLE", "Clip").replace("%(ext)s", "mp4"))
for name in (out.name + ".part", out.stem + ".f137.mp4.part", out.stem + ".f140.m4a", out.name + ".ytdl"):
    (out.parent / name).write_text("x")
time.sleep(60)
"""


def _cancel_after(seconds: float):
    t0 = time.monotonic()
    return lambda: time.monotonic() - t0 > seconds


def test_run_cancellable_returns_the_exit_code():
    assert source._run_cancellable([sys.executable, "-c", "raise SystemExit(3)"], lambda: False) == 3


def test_run_cancellable_kills_the_process_at_once():
    t0 = time.monotonic()
    assert source._run_cancellable([sys.executable, "-c", "import time; time.sleep(60)"], _cancel_after(0.3)) is None
    assert time.monotonic() - t0 < 10


def test_remove_partial_files_keeps_the_rest(tmp_path):
    video = tmp_path / "001 - Clip.mp4"
    partial = ["001 - Clip.mp4.part", "001 - Clip.f137.mp4", "001 - Clip.f140.m4a.part", "001 - Clip.mp4.ytdl", "001 - Clip.webp",
               "001 - Clip.mp4.part-Frag3", "001 - Clip.temp.mp4"]
    kept = ["001 - Clip.fr.srt", "002 - Other.mp4.part", "001 - Clip [x].mp4", "notes.txt"]
    for n in partial + kept:
        (tmp_path / n).write_text("x")
    removed = source.remove_partial_files(video)
    assert sorted(p.name for p in removed) == sorted(partial)
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(kept)


def test_remove_partial_files_with_brackets_in_the_name(tmp_path):
    video = tmp_path / "Clip [1080p].mp4"
    (tmp_path / "Clip [1080p].mp4.part").write_text("x")
    assert [p.name for p in source.remove_partial_files(video)] == ["Clip [1080p].mp4.part"]


def test_download_youtube_cancelled_kills_and_cleans(tmp_path, monkeypatch):
    fake = tmp_path / "fake_ytdlp.py"
    fake.write_text(FAKE_YTDLP, encoding="utf-8")
    folder = tmp_path / "videos"
    folder.mkdir()
    monkeypatch.setattr(source, "youtube_info", lambda url, command, folder, name: {
        "title": "Clip", "duration": 10, "video": folder / name.replace("TITLE", "Clip").__add__(".mp4")})
    lines = []
    t0 = time.monotonic()
    with pytest.raises(source.DownloadCancelled) as stopped:
        source.download_youtube("https://example.org/v", [sys.executable, str(fake)], lines.append, folder, "001 - TITLE",
                                cancelled=lambda: any(folder.glob("*.ytdl")))   # cancelled as soon as it has started
    assert time.monotonic() - t0 < 20
    assert list(folder.iterdir()) == [] and stopped.value.partial is None
    assert "4" in lines[-1]                                         # the 4 partial files deleted


def test_download_youtube_paused_keeps_the_partial_files(tmp_path, monkeypatch):
    fake = tmp_path / "fake_ytdlp.py"
    fake.write_text(FAKE_YTDLP, encoding="utf-8")
    folder = tmp_path / "videos"
    folder.mkdir()
    monkeypatch.setattr(source, "youtube_info", lambda url, command, folder, name: {
        "title": "Clip", "duration": 10, "video": folder / name.replace("TITLE", "Clip").__add__(".mp4")})
    with pytest.raises(source.DownloadCancelled) as stopped:
        source.download_youtube("https://example.org/v", [sys.executable, str(fake)], lambda s: None, folder, "001 - TITLE",
                                cancelled=lambda: any(folder.glob("*.ytdl")), keep_partial=lambda: True)
    assert stopped.value.partial == folder / "001 - Clip.mp4"       # yt-dlp resumes from them
    assert len(list(folder.iterdir())) == 4


def test_a_name_too_long_is_said_plainly(tmp_path, monkeypatch):
    fake = tmp_path / "fake_ytdlp.py"
    fake.write_text("print(\"ERROR: unable to open for writing: [Errno 36] File name too long: 'x'\"); raise SystemExit(1)", encoding="utf-8")
    monkeypatch.setattr(source, "youtube_info", lambda url, command, folder, name: {"title": "Clip", "video": tmp_path / "Clip.mp4"})
    lines = []
    with pytest.raises(source.SourceError) as failed:
        source.download_youtube("https://example.org/v", [sys.executable, str(fake)], lambda s: None, tmp_path, on_line=lines.append)
    assert str(failed.value) == source.i18n.t("err.name_too_long") and "Errno 36" in lines[0]


def test_the_title_is_cut_to_what_a_file_name_holds(tmp_path, monkeypatch):
    monkeypatch.setattr(source.os, "name", "posix")
    assert source.name_template("/videos", "001 - ") == "001 - %(title).180B" and source.name_template() == source.TITLE
    monkeypatch.setattr(source.os, "name", "nt")                    # Windows: the whole path counts too
    assert source.name_template("D:/videos", "001 - ") == "001 - %(title).180B"
    deep = "D:/" + "d" * 147                                        # 150 characters
    assert source.name_template(deep, "001 - ") == f"001 - %(title).{259 - 150 - 1 - 6 - 32}B"
    with pytest.raises(source.SourceError):
        source.name_template("D:/" + "d" * 197, "001 - ")           # no room left for a title
    monkeypatch.undo()


def test_remove_partial_files_can_keep_a_thumbnail(tmp_path):
    video = tmp_path / "001 - Clip.mp4"
    for n in ("001 - Clip.f137.mp4.part", "001 - Clip.jpg"):
        (tmp_path / n).write_text("x")
    assert [p.name for p in source.remove_partial_files(video, thumbnails=False)] == ["001 - Clip.f137.mp4.part"]
    assert (tmp_path / "001 - Clip.jpg").exists()


def test_download_youtube_cancelled_before_start(tmp_path, monkeypatch):
    monkeypatch.setattr(source, "youtube_info", lambda *a: {"title": "Clip", "video": tmp_path / "Clip.mp4"})
    monkeypatch.setattr(source, "_run_cancellable", lambda *a: pytest.fail("yt-dlp started"))
    with pytest.raises(source.DownloadCancelled):
        source.download_youtube("https://example.org/v", ["yt-dlp"], lambda s: None, tmp_path, cancelled=lambda: True)


def test_playlist_stops_on_the_video_being_downloaded(tmp_path, monkeypatch):
    """Cancelled during the 2nd video: no 3rd, and the 2nd is not counted as a failure."""
    stop = threading.Event()
    calls = []

    def fake(url, command, log, folder, name, cancelled):
        calls.append(url)
        if url.endswith("2"):
            stop.set()
            raise source.DownloadCancelled("cancelled")
        return folder / (url[-1] + ".mp4")

    monkeypatch.setattr(source, "download_youtube", fake)
    info = {"entries": [{"url": f"https://example.org/{i}", "title": str(i)} for i in (1, 2, 3)]}
    files, failures = source.download_playlist(info, ["yt-dlp"], tmp_path, lambda s: None, stop.is_set)
    assert calls == ["https://example.org/1", "https://example.org/2"]
    assert [f.name for f in files] == ["1.mp4"] and failures == []


def test_playlist_download_tells_how_many_videos_are_there(tmp_path, monkeypatch):
    info = {"title": "Ma liste", "entries": [{"url": "https://example.org/1", "title": "1"}, {"url": "https://example.org/2", "title": "2"}]}
    monkeypatch.setattr(source, "list_info", lambda urls, command, title: info)

    def fake(info, command, folder, log, cancelled, progress):
        d.cancel()                                                  # cancelled during the 2nd video
        return [folder / "001 - 1.mp4"], []

    monkeypatch.setattr(source, "download_playlist", fake)
    d = PlaylistDownload("", ["yt-dlp"], tmp_path, urls=["https://example.org/1", "https://example.org/2"], title="Ma liste")
    d.run()
    assert d.state == DownloadState.CANCELLED
    assert d.to_dict()["downloaded"] == 1 and d.to_dict()["folder"].endswith("/Ma liste")


def test_pipeline_turns_a_cancelled_download_into_cancelled(tmp_path, monkeypatch):
    from subtitles import pipeline

    def stopped(*a, **k):
        raise source.DownloadCancelled("cancelled")

    monkeypatch.setattr(source, "download_youtube", stopped)
    cfg = {"output_dir": str(tmp_path), "transcriptions": [{"id": "t", "url": "http://127.0.0.1:1"}], "active_transcription": "t",
           "youtube": {"command": ["yt-dlp"]}}
    r = pipeline.Request(source="https://example.org/v", cfg=cfg)
    with pytest.raises(pipeline.Cancelled):
        pipeline.prepare(r, lambda s: None)
