"""yt-dlp: its verbose header read (version, channel, variant, age), what updates itself, the hint otherwise; the update
run in a thread with its lines; the routes refuse an update during a download or for a yt-dlp that does not update itself."""
import datetime as dt
import io
import time

from subtitles.web.routes import system as system_routes
from subtitles.web import service
from subtitles.web import router
from subtitles import config, ytdlp

WSL = """[debug] Command-line config: ['-v']
[debug] yt-dlp version nightly@2026.10.29.232701 from yt-dlp/yt-dlp-nightly-builds [b77c9304e] (zip)
[debug] Python 3.12.3"""
WIN = "[debug] yt-dlp version stable@2026.08.27 from yt-dlp/yt-dlp [8377aa955] (win_exe)"
PIP = "[debug] yt-dlp version 2026.01.02 [abc] (pip)"


def test_header_read():
    d = ytdlp.parse(WSL, now=dt.date(2026, 11, 14))
    assert d == {"version": "2026.10.29", "channel": "nightly", "variant": "zip", "age_days": 16, "old": False, "self_update": True}
    w = ytdlp.parse(WIN, now=dt.date(2026, 11, 14))
    assert w["variant"] == "win_exe" and w["self_update"] and w["age_days"] == 79 and w["old"]
    p = ytdlp.parse(PIP, now=dt.date(2026, 11, 14))
    assert p["channel"] == "stable" and not p["self_update"]
    assert ytdlp.parse("yt-dlp: command not found") is None


def test_hint_for_what_does_not_update_itself():
    assert "pipx" in ytdlp.hint("pip", ["yt-dlp"]) and not ytdlp.hint("pip", ["yt-dlp"]).startswith(ytdlp.i18n.t("ytdlp.in_wsl"))
    assert ytdlp.hint("pip", ["wsl", "-e", "yt-dlp"]).startswith(ytdlp.i18n.t("ytdlp.in_wsl"))
    assert "apt" in ytdlp.hint("debian", ["yt-dlp"])


def test_info_cached_and_hint(monkeypatch):
    monkeypatch.setattr(config, "load", lambda *_a, **_k: {"youtube": {"command": ["yt-dlp"]}})
    calls = []

    def run(args, **_k):
        calls.append(args)
        return type("R", (), {"stderr": PIP, "stdout": ""})()
    ytdlp._cache.clear()
    d = ytdlp.info(run=run)
    assert d["found"] and d["variant"] == "pip" and "pipx" in d["hint"] and calls == [["yt-dlp", "--ignore-config", "-v"]]
    ytdlp.info(run=run)
    assert len(calls) == 1                                       # within the hour: the same answer
    ytdlp.info(fresh=True, run=run)
    assert len(calls) == 2


def test_info_without_yt_dlp(monkeypatch):
    monkeypatch.setattr(config, "load", lambda *_a, **_k: {"youtube": {"command": ["nowhere-yt-dlp"]}})

    def run(*_a, **_k):
        raise FileNotFoundError("nowhere-yt-dlp")
    ytdlp._cache.clear()
    assert ytdlp.info(fresh=True, run=run) == {"found": False, "command": "nowhere-yt-dlp"}


def test_update_runs_and_keeps_its_lines():
    class P:
        def __init__(self, args, **_k):
            self.args = args
            self.stdout = io.StringIO("Current version: nightly@2026.10.29\nUpdated yt-dlp to nightly@2026.11.13\n")

        def wait(self):
            return 0
    ytdlp._cache[("x",)] = (time.time(), {})
    u = ytdlp.Update(["wsl", "yt-dlp"], popen=P).start()
    u.thread.join(5)
    d = u.to_dict()
    assert d["state"] == "done" and d["code"] == 0 and d["command"] == "wsl yt-dlp -U" and d["lines"][-1].startswith("Updated")
    assert ytdlp._cache == {}                                    # the version is asked again


def test_update_routes(monkeypatch):
    monkeypatch.setattr(system_routes, "YTDLP_UPDATE", None)
    monkeypatch.setattr(service.manager.projects.fetching, "downloads", {"a": object()})
    try:
        system_routes.ytdlp_update(None)
        raise AssertionError("refused during a download")
    except router.RequestError as e:
        assert e.code == 409
    monkeypatch.setattr(service.manager.projects.fetching, "downloads", {})
    monkeypatch.setattr(ytdlp, "info", lambda fresh=False: {"found": True, "self_update": False, "hint": "pipx upgrade yt-dlp"})
    try:
        system_routes.ytdlp_update(None)
        raise AssertionError("refused for pip")
    except router.RequestError as e:
        assert e.code == 409 and "pipx" in str(e)
    assert system_routes.ytdlp_update_state(None) == (200, {"state": "none", "lines": []})
