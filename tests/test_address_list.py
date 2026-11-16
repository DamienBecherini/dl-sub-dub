"""Lists of addresses (several lines in the page, a .txt file, several addresses on the command line): read like one
playlist made of the videos and playlists given, downloaded into one folder, then processed as a project."""
import json
from types import SimpleNamespace

import pytest

from subtitles import __main__ as cli, source
from subtitles.states import DownloadState
from subtitles.web.playlist import PlaylistDownload
from helpers import titled

YTDLP = ["yt-dlp"]
VIDEOS = {"a": "First", "b": "Second", "c": "Third"}
PLAYLIST = [{"_type": "url", "id": "b", "url": "https://v.example/b", "title": "Second"},
            {"_type": "url", "id": "c", "url": "https://v.example/c", "title": "Third"}]


class FakeYtdlp:
    """https://v.example/<id>: one video; https://v.example/list: a playlist of b and c; https://v.example/gone: an error."""

    def __init__(self):
        self.downloads = []

    def __call__(self, cmd, **_kw):
        args = cmd[1:]
        ident = args[-1].rsplit("/", 1)[-1]
        if ident == "gone":
            return SimpleNamespace(returncode=1, stdout="", stderr="ERROR: Video unavailable")
        if "-J" in args:
            d = ({"_type": "playlist", "id": "PL", "title": "List", "entries": PLAYLIST} if ident == "list"
                 else {"_type": "video", "id": ident, "title": VIDEOS[ident], "webpage_url": f"https://v.example/{ident}"})
            return SimpleNamespace(returncode=0, stdout=json.dumps(d), stderr="")
        name = titled(args[args.index("-o") + 1], VIDEOS[ident]).replace("%(ext)s", "mp4")
        if "--print" in args:
            return SimpleNamespace(returncode=0, stdout=json.dumps({"id": ident, "title": "t", "duration": 1, "filename": name}) + "\n", stderr="")
        self.downloads.append(ident)
        open(name, "wb").write(b"video")
        return SimpleNamespace(returncode=0)


@pytest.fixture
def ytdlp(monkeypatch):
    fake = FakeYtdlp()
    monkeypatch.setattr(source.subprocess, "run", fake)
    monkeypatch.setattr(source, "_run_cancellable", lambda cmd, cancelled: fake(cmd).returncode)   # the download itself
    monkeypatch.setattr(source, "tool", lambda name: name)
    return fake


def test_lines_of_a_list():
    text = "\ufeff https://v.example/a \n\n# a comment\nhttps://v.example/b\r\nhttps://v.example/a\n"
    assert source.address_lines(text) == ["https://v.example/a", "https://v.example/b"]
    assert source.not_urls(["https://v.example/a", "D:/video.mp4", "www.example.com"]) == ["D:/video.mp4", "www.example.com"]


def test_a_text_file_of_addresses(tmp_path):
    f = tmp_path / "Courses.txt"
    f.write_text("\ufeffhttps://v.example/a\nhttps://v.example/list\n", encoding="utf-8")
    assert source.is_address_file(str(f)) and not source.is_address_file(str(tmp_path / "missing.txt"))
    assert source.read_addresses(f) == ["https://v.example/a", "https://v.example/list"]


def test_a_list_reads_videos_and_playlists_in_order(ytdlp):
    info = source.list_info(["https://v.example/a", "https://v.example/list", "https://v.example/gone"], YTDLP, "Mine")
    assert info["title"] == "Mine" and not info["with_video"]
    assert [e["title"] for e in info["entries"]] == ["First", "Second", "Third", "https://v.example/gone"]
    assert ytdlp.downloads == []


def test_background_download_of_a_list(ytdlp, tmp_path):
    job = PlaylistDownload("", YTDLP, tmp_path, ["https://v.example/a", "https://v.example/list", "https://v.example/gone"], "Mine")
    job.run()
    d = job.to_dict()
    assert d["state"] == DownloadState.DONE and d["total"] == 4 and len(d["failures"]) == 1
    assert sorted(p.name for p in (tmp_path / "Mine").iterdir()) == ["001 - First.mp4", "002 - Second.mp4", "003 - Third.mp4"]


def test_list_title_has_the_date():
    assert "2026-11-13" in source.list_title(1794600000)


@pytest.fixture
def cli_env(ytdlp, tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(cli.config, "load", lambda: {"output_dir": str(tmp_path), "youtube": {"command": YTDLP}})
    monkeypatch.setattr(cli.folder_cli, "run", lambda path, cfg, log, **kw: seen.update(path=path, kw=kw) or 0)
    return seen


def test_command_line_several_addresses_make_one_project(cli_env, tmp_path, monkeypatch):
    monkeypatch.setattr(source, "list_title", lambda when=None: "List 2026-11-13 23-50")
    assert cli.main(["https://v.example/a", "https://v.example/c", "--languages", "fr"]) == 0
    assert cli_env["path"].endswith("List 2026-11-13 23-50") and cli_env["kw"]["languages"] == ["fr"]
    assert sorted(p.name for p in (tmp_path / "List 2026-11-13 23-50").iterdir()) == ["001 - First.mp4", "002 - Third.mp4"]


def test_command_line_file_of_addresses_names_the_folder(cli_env, tmp_path):
    f = tmp_path / "Courses.txt"
    f.write_text("https://v.example/list\n", encoding="utf-8")
    assert cli.main([str(f)]) == 0
    assert cli_env["path"].endswith("Courses")
    assert sorted(p.name for p in (tmp_path / "Courses").iterdir()) == ["001 - Second.mp4", "002 - Third.mp4"]


def test_command_line_refuses_lines_that_are_not_addresses(cli_env):
    assert cli.main(["https://v.example/a", "D:/video.mp4"]) == 1 and "path" not in cli_env
