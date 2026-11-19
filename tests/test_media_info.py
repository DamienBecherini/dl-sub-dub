"""What a video file holds (Information tab): ffprobe read (picture, audio and subtitle tracks, the cover picture left
out), subtitle files next to it (brackets in the name), kept while the file does not change; the thumbnail made once.
A video's own engines: its own when chosen, the project's otherwise."""
import json
from types import SimpleNamespace

from subtitles.project.choices import own
from subtitles import config, media

PROBE = {"format": {"duration": "60.5"}, "streams": [
    {"index": 0, "codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080, "avg_frame_rate": "30000/1001"},
    {"index": 1, "codec_type": "audio", "codec_name": "aac", "channels": 2, "tags": {"language": "fra"}, "disposition": {"default": 1}},
    {"index": 2, "codec_type": "audio", "codec_name": "eac3", "channels": 6, "tags": {"language": "eng", "title": "VO"}},
    {"index": 3, "codec_type": "subtitle", "codec_name": "subrip", "tags": {"language": "eng"}},
    {"index": 4, "codec_type": "video", "codec_name": "mjpeg", "disposition": {"attached_pic": 1}}]}


def test_info_of_a_file(tmp_path):
    video = tmp_path / "Ep [1].mkv"
    video.write_bytes(b"x" * 10)
    (tmp_path / "Ep [1].fr.srt").write_text("1", encoding="utf-8")
    (tmp_path / "Ep [1].srt").write_text("1", encoding="utf-8")
    (tmp_path / "Other.fr.srt").write_text("1", encoding="utf-8")
    calls = []

    def run(args, **_k):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout=json.dumps(PROBE), stderr="")
    media._cache.clear()
    d = media.info(video, run=run)
    assert d["size"] == 10 and d["duration"] == 60.5
    assert d["video"] == {"width": 1920, "height": 1080, "codec": "h264", "fps": 29.97}           # not the cover picture
    assert [(a["language"], a["channels"], a["default"]) for a in d["audio"]] == [("fra", 2, True), ("eng", 6, False)]
    assert d["subtitles"] == [{"language": "eng", "codec": "subrip", "title": ""}]
    assert sorted((s["language"], s["name"]) for s in d["sidecar"]) == [("?", "Ep [1].srt"), ("fr", "Ep [1].fr.srt")]
    media.info(video, run=run)
    assert len(calls) == 1                                                  # same file: not read again


def test_thumbnail_made_once(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "state_dir", lambda: tmp_path / "user")
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    calls = []

    def run(args, **_k):
        calls.append(args)
        open(args[-1], "wb").write(b"jpeg")
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    a = media.thumbnail(video, 300, run=run)
    assert a.read_bytes() == b"jpeg" and "-ss" in calls[0] and calls[0][calls[0].index("-ss") + 1] == "30.00"
    assert media.thumbnail(video, 300, run=run) == a and len(calls) == 1


def test_a_video_own_engines():
    run = SimpleNamespace(options={"translator": "local", "transcription": "", "languages": ["fr"]})
    item = SimpleNamespace(overrides={"translator": "claude"})
    assert own(run.options, item, "translator") == "claude"
    assert own(run.options, item, "languages") == ["fr"]
    item.overrides["translator"] = ""
    assert own(run.options, item, "translator") == "local"


def test_the_address_written_by_ytdlp_read_back():
    assert media.source_address({"PURL": "https://www.youtube.com/watch?v=abc"}) == "https://www.youtube.com/watch?v=abc"
    assert media.source_address({"comment": "https://peertube.example/w/x", "purl": ""}) == "https://peertube.example/w/x"
    assert media.source_address({"comment": "a nice video, see https://x"}) == ""        # a text, not an address
    assert media.source_address({}) == ""
