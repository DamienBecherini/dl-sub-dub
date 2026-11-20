"""The service behind the tests of the page (tests/test_page_*.py, fixture `studio` of conftest.py): the real one, at
work on a port of its own, on real small videos, writing its real files. Nothing of the user is touched (settings,
history, thumbnails: everything goes to the temporary folder of the test), and nothing leaves the machine: only its
edge is replaced, each by the least possible:

- the transcription server (`transcription.transcribe`: the words of what the test says the video says);
- the model (`translation.create`: it gives each line back marked "TR ", answers the question of the language, and
  proposes what the test gives it for an AI review);
- yt-dlp (`source.describe`, `source.download_youtube`: an address is one small video; `ytdlp.info`: a recent one);
- the dubbing program (`dubbing.dub`: a small voice file; the MKV is really made).

ffmpeg and ffprobe are really used (the audio extracted, the durations, the thumbnails, the MKV)."""
import json
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from subtitles import config, dubbing, language, source, steps, transcription, translation, ytdlp
from subtitles.project import Stage
from subtitles.web import server, service
from subtitles.web.manager import Manager
from subtitles.web.tasks import TaskQueue

# What a video says: English, with a name the engine "heard wrong" (Gary Iev) for the AI review, and too short to be
# judged doubtful (under 200 words).
TALK = ("Hello and welcome to this short episode. Today we talk about the work of Gary Iev and what it means for all of us. "
        "The rule is easy to keep in mind. You need three copies of your files on two kinds of disks. "
        "A disk can fail and a flat can burn. With three copies you do not lose your pictures. "
        "Gary Iev said it many times and he was right. Thank you for watching and see you soon.")
LOCAL = {"id": "local", "type": "openai", "name": "Local", "url": "http://127.0.0.1:8080", "model": "qwen", "key": ""}
CLAUDE = {"id": "claude", "type": "claude", "name": "Claude", "model": "claude-sonnet-5-5", "key": "env:ANTHROPIC_API_KEY"}
NONE = {"id": "none", "type": "none", "name": "Pas de traduction"}
ADDRESS = "https://videos.example.invalid/watch/abc"       # the only address the fake yt-dlp knows: "A talk online"
SECONDS = 36                                               # the length of the videos of the tests

_LINE = re.compile(r"^(\d+)\. (.*)$", re.M)


def media(folder: Path) -> tuple[Path, Path]:
    """A video of 36 seconds (a black picture, a tone: a few kilobytes) and a voice file of the same length, made once
    by ffmpeg. As long as what TALK says: the MKV is checked against the duration of its video."""
    video, voice = folder / "small.mp4", folder / "small.m4a"
    if not video.is_file():
        quiet = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "check": True}
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=black:s=64x64:d={SECONDS}:r=2", "-f", "lavfi",
                        "-i", f"sine=frequency=440:duration={SECONDS}", "-shortest", "-pix_fmt", "yuv420p", str(video)], **quiet)
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"sine=frequency=220:duration={SECONDS}", str(voice)], **quiet)
    return video, voice


def words_of(said: str) -> list[dict]:
    """Each word with its time, a silence after each sentence, as a transcription server gives them."""
    words, at = [], 0.0
    for sentence in re.findall(r"[^.]+\.", said):
        for w in sentence.split():
            words.append({"word": w, "start": round(at, 2), "end": round(at + 0.3, 2)})
            at += 0.35
        at += 0.8
    return words


class Model:
    """The model of every translator of the test: it records what it is asked and for whom."""

    def __init__(self, studio, engine: dict):
        self.studio, self.engine = studio, engine

    def prepare(self):
        pass

    def finish(self):
        pass

    def complete(self, system, request, schema=None, effort=None):
        s = self.studio
        if system == language.ASK_SPOKEN:
            s.asked.append(("language", self.engine["id"]))
            return s.spoken
        if schema is not None:                                         # an AI review: what the test prepared, else nothing to fix
            s.asked.append(("review", self.engine["id"]))
            return json.dumps({"entries": s.review.pop(0) if s.review else []})
        s.asked.append(("translation", self.engine["id"]))
        assert s.free.wait(30), "the test never let the translation go on"
        if s.failing:
            raise translation.TranslationError(s.failing)
        lines = _LINE.findall(request.split("Lignes à traduire :")[-1])
        return "\n".join(f"{n}. TR {text}" for n, text in lines)


class Studio:
    def __init__(self, tmp_path: Path, small: tuple[Path, Path]):
        self.root, self.small = tmp_path, small
        self.folder = tmp_path / "videos"                              # where the test puts its videos
        self.folder.mkdir()
        self.said: dict[str, str] = {}                                 # working folder of a video → what it says
        self.asked: list[tuple[str, str]] = []                         # (what, engine id) for each request to a model
        self.transcribed: list[str] = []                               # the videos sent to the transcription server
        self.downloaded: list[str] = []                                # the addresses downloaded
        self.dubbed: list[tuple[str, str]] = []                        # (video, language) for each voice made
        self.review: list[list[dict]] = []                             # the answers of the model to the next AI reviews
        self.spoken = "ar"                                             # its answer to "which language is spoken?"
        self.failing = ""                                              # a message: the translation fails with it
        self.free = threading.Event()                                  # cleared by a test: the translation waits for it
        self.free.set()
        self.url = ""
        self.manager: Manager | None = None

    # --- what the test prepares ---

    def video(self, name: str = "Episode 1.mp4", said: str = TALK) -> Path:
        """A real small video in the folder of the test, which "says" `said`."""
        file = self.folder / name
        shutil.copyfile(self.small[0], file)
        self.said[source.slug(file.stem)] = said
        return file

    def settings(self, **changes) -> dict:
        """Changes the settings of the user (the file the service reads)."""
        cfg = json.loads(config.USER_FILE.read_text(encoding="utf-8")) if config.USER_FILE.is_file() else {}
        cfg.update(changes)
        config.USER_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")
        return cfg

    def project(self, *videos: Path, stages=(Stage.SUBTITLES,), **options):
        """A project of these videos, not started, as the page makes one."""
        return self.manager.projects.create(self.folder.as_posix(), [{"path": v.as_posix(), "title": v.name} for v in videos],
                                            {"translator": "local", **options}, "Mine", stages=list(stages), start=False)

    def restart(self, page):
        """The service stops and starts again, as after a reboot: what it knew of the projects is only in their files.
        The page is taken away meanwhile (a request answered half by the old service, half by the new one, is not
        something that happens)."""
        page.goto("about:blank")
        self.manager.close()
        self.manager = Manager(TaskQueue(history=[]))
        self._monkeypatch.setattr(service, "manager", self.manager)

    def srt(self, video: Path, code: str) -> str:
        """The subtitles of a video in a language, as written next to it ("" when there is none)."""
        file = video.with_name(f"{video.stem}.{code}.srt")
        return file.read_text(encoding="utf-8") if file.is_file() else ""

    # --- the edge, replaced ---

    def transcribe(self, server_url, audio, language=None, timeout_s=0, key="", cancelled=lambda: False):
        said = self.said[Path(audio).parent.name]
        self.transcribed.append(Path(audio).parent.name)
        return {"words": words_of(said), "segments": [], "text": said, "language": ""}

    def describe(self, url, command):
        if url != ADDRESS:
            raise source.SourceError("no such address in the tests")
        return {"playlist": None, "entries": [{"url": url, "title": "A talk online", "duration": SECONDS}]}

    def download(self, url, command, log=print, folder=None, name=None, cancelled=lambda: False, extra=(), on_line=None, keep_partial=lambda: False):
        self.downloaded.append(url)
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        file = folder / "A talk online.mp4"
        shutil.copyfile(self.small[0], file)
        self.said[source.slug(file.stem)] = TALK
        return file

    def dub(self, cfg, video, work, source_srt, target_srt, target, log=print, cancelled=lambda: False, redo=False, progress=None, phase=None):
        self.dubbed.append((Path(video).name, target))
        output = steps.dubbing_file(work, target)
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.small[1], output)
        return output


def serve(tmp_path: Path, monkeypatch, small: tuple[Path, Path]):
    """The fixture `studio`: a service at work for the time of a test (a generator, as a fixture is)."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "state"))        # log, thumbnails, location.json: not the user's
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "state"))
    user = tmp_path / "state" / "subtitles"
    user.mkdir(parents=True)
    monkeypatch.setattr(config, "USER_FILE", user / "config.json")
    studio = Studio(tmp_path, small)
    studio._monkeypatch = monkeypatch
    studio.settings(ui_language="fr", output_dir=str(tmp_path / "out"), target_languages=["fr"],
                    transcriptions=[{"id": "t", "name": "Serveur de test", "url": "http://127.0.0.1:9", "key": ""}], active_transcription="t",
                    translators=[LOCAL, CLAUDE, NONE], active_translator="local", destination={"next_to_video": True, "output_dir": False},
                    youtube={"command": ["yt-dlp"], "download_dir": str(tmp_path / "downloads")},
                    dubbing={"enabled": False, "command": [sys.executable], "model": "test/voice", "gpu_sharing": "together"})
    monkeypatch.setattr(transcription, "transcribe", studio.transcribe)
    monkeypatch.setattr(transcription, "health", lambda url, timeout_s=5: "ready")
    monkeypatch.setattr(translation, "create", lambda m, log=print: Model(studio, m))
    monkeypatch.setattr(source, "describe", studio.describe)
    monkeypatch.setattr(source, "download_youtube", studio.download)
    monkeypatch.setattr(dubbing, "dub", studio.dub)
    monkeypatch.setattr(ytdlp, "info", lambda fresh=False, run=None: {"found": True, "command": "yt-dlp", "version": "2026.11.01", "channel": "stable",
                                                                      "variant": "pip", "age_days": 1, "old": False, "self_update": False, "hint": ""})
    studio.manager = Manager(TaskQueue(history=[]))
    monkeypatch.setattr(service, "manager", studio.manager)
    httpd = server.Server(("127.0.0.1", 0), server.Handler)
    httpd.handle_error = lambda request, client: None                  # a page that left mid-answer: not worth a traceback in the output
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    studio.url = f"http://127.0.0.1:{httpd.server_address[1]}"
    yield studio
    studio.free.set()
    studio.manager.close()
    httpd.shutdown()
    httpd.server_close()


def small_media(folder: Path) -> tuple[Path, Path]:
    """`media`, or the tests skipped without ffmpeg (they fail where the tests of the page are required)."""
    import os
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        (pytest.fail if os.environ.get("PAGE_TESTS") == "required" else pytest.skip)("tests of the page: ffmpeg and ffprobe are needed")
    return media(folder)


def until(page, done, what: str = "", seconds: float = 10):
    """Waits for something of the service to be true (what a click of the page asked for, answered a moment later)."""
    for _ in range(int(seconds * 20)):
        if done():
            return
        page.wait_for_timeout(50)
    raise AssertionError(f"never true within {seconds:g} s: {what or done}")


def opened(page, studio, run=None):
    """The page on this project (it keeps the project it shows in the browser), or on a new one."""
    if run is not None:
        page.add_init_script("try { localStorage.setItem('v2-page', JSON.stringify({projectId: '%s', view: 'compact'})); } "
                             "catch (e) { /* a blank page has no storage */ }" % run.id)
    page.goto(studio.url + "/")
    page.locator(".run-main").wait_for()
    return page
