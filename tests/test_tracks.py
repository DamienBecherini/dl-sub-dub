"""What a site offers for a video (summary of yt-dlp -J), the tracks chosen per video for its download, the
information read in the background, and the tracks added after the download."""
import pytest

from helpers import at
from subtitles.web import background
from subtitles.web import observed
from subtitles import config, source, tracks
from subtitles.project import Stage, StageState
from subtitles.web import downloads
from subtitles.web.manager import Manager
from subtitles.web.tasks import TaskQueue

S = StageState
A = "https://x.example/a"
INFO = {"title": "Squid", "duration": 1541, "uploader": "Chan", "upload_date": "20211124", "thumbnail": "https://img/x.jpg",
        "language": "en-US",
        "formats": [
            {"format_id": "249-drc", "vcodec": "none", "language": "en-US", "format_note": "English (US) original (default), low, DRC"},
            {"format_id": "139-9", "vcodec": "none", "language": "fr", "format_note": "French, low"},
            {"format_id": "233-4", "vcodec": "none", "language": "fr", "format_note": "Français - dubbed"},
            {"format_id": "140-2", "vcodec": "none", "language": "de", "format_note": "German (dubbed-auto)"},
            {"format_id": "137", "vcodec": "avc1", "height": 1080}, {"format_id": "136", "vcodec": "avc1", "height": 720},
            {"format_id": "160", "vcodec": "avc1", "height": 90}],
        "subtitles": {"en": [], "fr": [], "live_chat": []}, "automatic_captions": {"en-orig": [], "en": [], "fr": []}}


def test_summary_of_what_the_site_offers():
    s = tracks.summarize(INFO)
    assert s["heights"] == [1080, 720]
    assert s["audio"] == [{"language": "en-US", "kind": "original"}, {"language": "fr", "kind": "dubbed"}, {"language": "de", "kind": "auto"}]
    assert s["subtitles"] == ["en", "fr"] and s["auto_subtitles"] == ["en"]       # automatic: the spoken language only
    assert (s["title"], s["uploader"], s["upload_date"]) == ("Squid", "Chan", "20211124")
    alone = tracks.summarize({"formats": [{"vcodec": "none", "language": "it", "format_note": "low"}]})
    assert alone["audio"] == [{"language": "it", "kind": "original"}]              # a single track is the original


def test_kind_of_an_audio_track():
    assert [tracks.track_kind(n) for n in ("x original (default)", "Français - dubbed", "German (dubbed-auto)", "descriptive", "low")] \
        == ["original", "dubbed", "auto", "descriptive", ""]


def test_options_of_the_tracks_chosen(tmp_path):
    assert tracks.download_options(["yt-dlp"], tmp_path, "001 - %(title)s", 720, None, None) == []
    o = tracks.download_options(["yt-dlp"], tmp_path, "001 - %(title)s", 720, ["en-US", "fr"],
                                [{"language": "fr", "auto": False}, {"language": "en", "auto": True}])
    assert o[:2] == ["-f", "bv*[height<=720]+ba[language=en-US]+ba[language=fr]/bv*+ba[language=en-US]+ba[language=fr]"]
    assert "--audio-multistreams" in o and "--write-subs" in o and "--write-auto-subs" in o
    assert o[o.index("--sub-langs") + 1] == "fr,en-orig" and o[o.index("--convert-subs") + 1] == "srt"
    assert o[o.index("-o") + 1] == "subtitle:" + tmp_path.as_posix() + "/001 - %(title)s.site.%(ext)s"
    assert "--audio-multistreams" not in tracks.download_options(["yt-dlp"], tmp_path, "n", 0, ["fr"], None)


@pytest.fixture
def g(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "load", lambda: {"output_dir": str(tmp_path / "out"), "youtube": {"command": ["yt-dlp"], "max_height": 1080}})
    monkeypatch.setattr(downloads.DownloadJob, "start", lambda self: None)
    monkeypatch.setattr(source, "tool", lambda name: name)
    g = Manager(TaskQueue(history=[]), start=False)
    monkeypatch.setattr(observed, "measure_durations", lambda *_a: None)
    return g


def project(g, tmp_path):
    return g.projects.create(tmp_path.as_posix(), [{"source": A}], {}, "P", [Stage.DOWNLOAD], start=False)


def test_the_information_is_read_in_the_background(g, tmp_path, monkeypatch):
    monkeypatch.setattr(tracks, "site_info", lambda url, command: tracks.summarize(INFO))
    run = project(g, tmp_path)
    started = []
    monkeypatch.setattr(background, "spawn", lambda name, target, *args: started.append((target, args)))
    g.projects.feed()                                                        # suspended projects too
    assert len(started) == 1
    target, args = started[0]
    target(*args)
    a = run.project.items[0]
    assert a.facts["site"]["heights"] == [1080, 720] and a.title == "Squid" and a.facts["duration"] == 1541


def test_a_failed_reading_can_be_done_again(g, tmp_path):
    run = project(g, tmp_path)
    a = run.project.items[0]
    g.projects.fetching.read_info(run, a)                                            # the tests never read a site: an error
    assert "site_error" in a.facts and "site" not in a.facts
    assert g.projects.fetching.read_info_again(run.id, a.id) and "site_error" not in a.facts


def test_settings_of_one_video_and_of_the_project(g, tmp_path):
    run = project(g, tmp_path)
    a = run.project.items[0]
    assert g.projects.set_item_settings(run.id, a.id, {"max_height": 480, "audio": ["en-US", "fr"]})
    assert a.overrides == {"max_height": 480, "audio": ["en-US", "fr"]}
    assert g.projects.set_item_settings(run.id, a.id, {"max_height": None}) and "max_height" not in a.overrides
    assert not g.projects.set_item_settings(run.id, a.id, {"output_dir": "C:/"})        # not a choice of a video
    assert g.projects.set_project_settings(run.id, {"max_height": 720}) and run.options["max_height"] == 720
    assert g.projects.set_project_settings(run.id, {"max_height": None}) and "max_height" not in run.options


def test_a_track_whose_language_is_not_a_plain_code_is_refused(tmp_path):
    """The language comes from the site: it names a file and goes to yt-dlp, so "../../x" must never get there."""
    for bad in ("../../x", "fr/../x", "en US", "%(title)s", "fr]+b", "", None, "x" * 36, "-fr"):
        assert not tracks.is_language(bad)
        with pytest.raises(source.SourceError):
            tracks.format_choice(720, ["fr", bad])
        with pytest.raises(source.SourceError):
            tracks.subtitle_options(["yt-dlp"], tmp_path, "001 - x", [{"language": bad, "auto": False}])
        with pytest.raises(source.SourceError):
            tracks.add_audio("https://x.example/a", ["yt-dlp"], tmp_path / "v.mp4", bad)
    assert all(tracks.is_language(ok) for ok in ("fr", "en-US", "zh-Hans", "es-419", "pt_BR", "fil"))
    info = tracks.summarize({"title": "t", "formats": [{"vcodec": "none", "language": "fr"}, {"vcodec": "none", "language": "../../x"}],
                             "subtitles": {"en": [], "../y": [], "live_chat": []}, "automatic_captions": {"fr-orig": [], "..-orig": []}})
    assert [a["language"] for a in info["audio"]] == ["fr"] and info["subtitles"] == ["en"] and info["auto_subtitles"] == ["fr"]


def test_the_download_takes_the_tracks_chosen(g, tmp_path, monkeypatch):
    seen = []

    def download(url, command, log, folder, name, cancelled, extra=(), on_line=None, keep_partial=None):
        seen.append(list(extra))
        f = tmp_path / "001 - Squid.mp4"
        f.write_bytes(b"x")
        return f
    monkeypatch.setattr(source, "download_youtube", download)
    run = project(g, tmp_path)
    a = run.project.items[0]
    g.projects.set_item_settings(run.id, a.id, {"audio": ["en-US", "fr"], "site_subtitles": [{"language": "fr", "auto": False}]})
    assert g.projects.run_item(run.id, a.id, Stage.DOWNLOAD)
    g.projects.fetching.downloads[a.id].run()
    assert "ba[language=fr]" in " ".join(seen[0]) and "--write-subs" in seen[0]
    assert a.facts["tracks"] == {"audio": ["en-US", "fr"], "subtitles": [{"language": "fr", "auto": False}]}
    assert a.state(Stage.DOWNLOAD) == S.DONE


def test_tracks_added_after_the_download(g, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(tracks, "fetch_subtitles", lambda url, command, folder, name, subs: calls.append(("subs", name, subs)))
    monkeypatch.setattr(tracks, "add_audio", lambda url, command, video, lang: calls.append(("audio", lang)))
    run = project(g, tmp_path)
    a = run.project.items[0]
    assert not g.projects.fetching.add_tracks(run.id, a.id, ["fr"], [])               # not downloaded yet
    a.video = (tmp_path / "001 - Squid.mp4").as_posix()
    at(a, Stage.DOWNLOAD, S.DONE)
    a.facts["site"] = tracks.summarize(INFO)
    started = []
    monkeypatch.setattr(background, "spawn", lambda name, target, *args: started.append((target, args)))
    assert g.projects.fetching.add_tracks(run.id, a.id, ["fr"], [{"language": "en", "auto": True}])
    assert a.facts["adding"] and not g.projects.fetching.add_tracks(run.id, a.id, ["de"], [])   # one addition at a time
    target, args = started[0]
    target(*args)
    assert calls == [("subs", "001 - Squid", [{"language": "en", "auto": True}]), ("audio", "fr")]
    assert a.facts["tracks"] == {"audio": ["en-US", "fr"], "subtitles": [{"language": "en", "auto": True}]}
    assert "adding" not in a.facts
