"""The application (python -m subtitles.app): the service it starts stops when its window closes, once its work is
done; the shortcut runs the Python of the installation; the Linux menu entry."""
import sys
from pathlib import Path

from subtitles import app
from subtitles.web import service
from subtitles.web.idle import IdleWatch

from test_server_routes import base, js  # noqa: F401  (base: the fixture of a real server)


def test_a_closed_window_stops_the_service_once_its_work_is_done():
    work = [True]
    w = IdleWatch(lambda: 3600, lambda: work[0], clock=lambda: 0)
    w.leave()
    assert not w.expired() and w.remaining_s() is None        # work running: it goes on, without its window
    work[0] = False
    assert w.expired() and w.remaining_s() == 0.0             # done: it stops at once, not an hour later
    w.leave(False)                                           # the window opened again before
    assert not w.expired()


def test_a_closed_window_without_a_timeout_stops_too():
    w = IdleWatch(lambda: 0, lambda: False, clock=lambda: 0)   # idle_min = 0: never by itself
    assert not w.expired()
    w.leave()
    assert w.expired()


def test_route_of_the_window_and_health(base, monkeypatch):  # noqa: F811
    url, _ = base
    monkeypatch.setattr(service, "idle", IdleWatch(lambda: 3600, lambda: False))
    monkeypatch.setattr(service, "launched_by_app", True)
    assert js(url + "/api/health")[1]["app"] is True
    assert js(url + "/api/window", "POST", {"open": False}) == (200, {"busy": False})
    assert service.idle.leaving
    assert js(url + "/api/window", "POST", {"open": True})[0] == 200
    assert not service.idle.leaving


def test_only_dl_sub_dub_is_taken_for_the_service():
    assert app.ours({"service": "subtitles", "state": "ready"})
    assert not app.ours({"service": "something else"}) and not app.ours(None)


def test_the_shortcut_runs_the_python_of_the_installation_without_a_console(tmp_path, monkeypatch):
    (tmp_path / "python.exe").write_text("")
    (tmp_path / "pythonw.exe").write_text("")
    monkeypatch.setattr(sys, "executable", str(tmp_path / "python.exe"))
    monkeypatch.setattr(app, "WINDOWS", True)
    assert app.command() == [str(tmp_path / "pythonw.exe"), "-m", "subtitles.app"]
    monkeypatch.setattr(app, "WINDOWS", False)
    assert app.command() == [str(tmp_path / "python.exe"), "-m", "subtitles.app"]


def test_menu_entry_of_linux(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "WINDOWS", False)
    monkeypatch.setattr(sys, "executable", "/home/me/dl sub dub/.venv/bin/python")
    entry = dict(line.split("=", 1) for line in app.desktop_entry().splitlines() if "=" in line)
    assert entry["Exec"] == f'"{Path("/home/me/dl sub dub/.venv/bin/python")}" -m subtitles.app'
    assert entry["Type"] == "Application" and entry["Terminal"] == "false" and entry["Name"] == "dl-sub-dub"
    assert Path(entry["Icon"]).exists() and Path(entry["Path"], "subtitles", "app.py").exists()
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert app.shortcut_places(desktop=False) == [tmp_path / "applications" / "dl-sub-dub.desktop"]


def test_no_shortcut_on_a_mac(monkeypatch, capsys):
    monkeypatch.setattr(sys, "platform", "darwin")
    assert app.shortcut("menu") == 1
    assert "subtitles.app" in capsys.readouterr().out


def test_the_icons_are_in_the_package():
    from importlib.resources import files
    names = {p.name for p in files("subtitles").joinpath("icons").iterdir()}
    assert {"dl-sub-dub.ico", "dl-sub-dub.png"} <= names
    assert "icons/*" in Path(__file__).parent.parent.joinpath("pyproject.toml").read_text(encoding="utf-8")
