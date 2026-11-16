"""The check of the MKV counts only the tracks it copies: a data stream of the video (some downloads carry one) is left
out, and was making a good MKV look incomplete."""
import pytest

from subtitles import muxing

VIDEO = {"streams": [{"codec_type": "video"}, {"codec_type": "audio"}, {"codec_type": "data", "codec_name": "bin_data"},
                     {"codec_type": "video", "codec_name": "png"}], "format": {"duration": "60.5"}}


def mkv(n):
    return {"streams": [{"codec_type": "audio"}] * n, "format": {"duration": "60.5"}}


def test_a_data_stream_is_not_expected_in_the_mkv(monkeypatch):
    monkeypatch.setattr(muxing, "probe", lambda path: mkv(6))         # video, audio, cover, dubbed audio, 2 subtitles
    muxing.verify("x.mkv", VIDEO, 3)
    monkeypatch.setattr(muxing, "probe", lambda path: mkv(5))
    with pytest.raises(muxing.MuxError):
        muxing.verify("x.mkv", VIDEO, 3)


COVERED = {"streams": [{"codec_type": "video"}, {"codec_type": "audio"},
                       {"codec_type": "video", "codec_name": "png", "disposition": {"attached_pic": 1}}], "format": {"duration": "60.5"}}


def test_a_cover_is_attached_not_a_track(monkeypatch, tmp_path):
    """yt-dlp's thumbnail (an attached picture) is not copied as a video track of one frame (VLC stalls on it): only
    the real video tracks are mapped, the cover goes in as the attachment of the MKV, which the check counts apart."""
    cmd = muxing.command("ffmpeg", tmp_path / "v.mp4", COVERED, tmp_path / "fr.m4a", "fr", "en", [], tmp_path / "v.mkv",
                         (tmp_path / "cover.png", "image/png"))
    maps = [cmd[i + 1] for i, c in enumerate(cmd) if c == "-map"]
    assert maps == ["0:0", "0:a", "0:s?", "1:a:0"]                  # the video track, never the cover (stream 2)
    at = cmd.index("-attach")
    assert cmd[at + 1] == str(tmp_path / "cover.png") and "mimetype=image/png" in cmd and "filename=cover.png" in cmd
    assert "-attach" not in muxing.command("ffmpeg", tmp_path / "v.mp4", COVERED, tmp_path / "fr.m4a", "fr", "en", [], tmp_path / "v.mkv")
    done = {"streams": [{"codec_type": "video"}, {"codec_type": "audio"}, {"codec_type": "audio"}, {"codec_type": "attachment"},
                        {"codec_type": "video", "codec_name": "png", "disposition": {"attached_pic": 1}}],
            "format": {"duration": "60.5"}}
    monkeypatch.setattr(muxing, "probe", lambda path: done)
    muxing.verify("x.mkv", COVERED, 1)                              # video, audio, dubbed audio; the cover apart


def test_a_picture_kept_as_a_track_is_a_cover_too(monkeypatch, tmp_path):
    """An MKV made before (the cover copied as a video track of one frame, not flagged as attached): the picture is a
    cover all the same, so it is not copied as a track again: the added tracks went to the end of the file and played
    silent."""
    import json
    from types import SimpleNamespace
    old = {"streams": [{"index": 0, "codec_type": "video", "codec_name": "vp9"}, {"index": 1, "codec_type": "video", "codec_name": "png"},
                       {"index": 2, "codec_type": "audio", "codec_name": "opus"}], "format": {"duration": "60.5"}}
    monkeypatch.setattr(muxing.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout=json.dumps(old), stderr=""))
    monkeypatch.setattr(muxing.source, "tool", lambda name: name)
    info = muxing.probe(tmp_path / "old.mkv")
    assert [muxing.is_cover(s) for s in info["streams"]] == [False, True, False]
    cmd = muxing.command("ffmpeg", tmp_path / "old.mkv", info, tmp_path / "en.m4a", "en", "fr", [], tmp_path / "new.mkv")
    assert [cmd[i + 1] for i, c in enumerate(cmd) if c == "-map"] == ["0:0", "0:a", "0:s?", "1:a:0"]
    alone = {"streams": [{"index": 0, "codec_type": "video", "codec_name": "mjpeg"}], "format": {"duration": "1"}}
    monkeypatch.setattr(muxing.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout=json.dumps(alone), stderr=""))
    assert not muxing.is_cover(muxing.probe(tmp_path / "m.avi")["streams"][0])      # a video in Motion JPEG is a video
