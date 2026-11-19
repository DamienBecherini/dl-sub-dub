"""The folder of the user's data (settings, history, speeds, last project): named by location.json in the fixed
folder, moved from Settings without losing nor overwriting anything, used at once by the running service."""
import json

import pytest

from subtitles import config, data_location
from subtitles.web import last_project, tasks, timing


@pytest.fixture
def fixed(tmp_path, monkeypatch):
    """The fixed folder in tmp, with the data in it (as before any move); the paths kept by the modules follow it."""
    folder = tmp_path / "LocalAppData" / "subtitles"
    folder.mkdir(parents=True)
    monkeypatch.setattr(config, "state_dir", lambda: folder)
    monkeypatch.setattr(config, "USER_FILE", folder / "config.json")
    monkeypatch.setattr(tasks, "HISTORY_FILE", folder / "history.json")
    monkeypatch.setattr(timing, "FILE", folder / "speeds.json")
    monkeypatch.setattr(last_project, "FILE", folder / "last_project.json")
    (folder / "config.json").write_text('{"output_dir": "X"}', encoding="utf-8")
    (folder / "history.json").write_text("[1, 2]", encoding="utf-8")
    (folder / "speeds.json").write_text("{}", encoding="utf-8")
    (folder / "app.log").write_text("log", encoding="utf-8")
    return folder


def test_without_location_the_data_is_in_the_fixed_folder(fixed):
    assert config.user_dir() == fixed


def test_location_names_the_folder(fixed, tmp_path):
    (fixed / "location.json").write_text(json.dumps({"data": str(tmp_path / "D")}), encoding="utf-8")
    assert config.user_dir() == tmp_path / "D"


def test_unreadable_location_falls_back_to_the_fixed_folder(fixed):
    (fixed / "location.json").write_text("{not json", encoding="utf-8")
    assert config.user_dir() == fixed


def test_move_copies_writes_the_location_then_deletes(fixed, tmp_path):
    target = tmp_path / "Damien" / "Sous-titres"
    message = data_location.move(str(target))
    assert "3 fichier(s)" in message
    assert json.loads((target / "history.json").read_text(encoding="utf-8")) == [1, 2]
    assert not (fixed / "config.json").exists() and not (fixed / "history.json").exists()
    assert (fixed / "app.log").exists()                                     # what belongs to the machine stays
    assert json.loads((fixed / "location.json").read_text(encoding="utf-8")) == {"data": str(target)}
    assert config.user_dir() == target


def test_the_running_service_uses_the_new_folder_at_once(fixed, tmp_path):
    target = tmp_path / "D"
    data_location.move(str(target))
    assert config.USER_FILE == target / "config.json" and tasks.HISTORY_FILE == target / "history.json"
    assert timing.FILE == target / "speeds.json" and last_project.FILE == target / "last_project.json"
    assert config.load()["output_dir"] == "X"                               # read from the new place
    config.save({"output_dir": "Y"})
    assert json.loads((target / "config.json").read_text(encoding="utf-8")) == {"output_dir": "Y"}


def test_a_folder_with_data_is_never_overwritten(fixed, tmp_path):
    target = tmp_path / "D"
    target.mkdir()
    (target / "config.json").write_text('{"output_dir": "theirs"}', encoding="utf-8")
    assert data_location.analyze(str(target))["has_data"]
    with pytest.raises(ValueError, match="contient déjà"):
        data_location.move(str(target))
    assert (fixed / "config.json").exists() and not (fixed / "location.json").exists()


def test_use_keeps_both_and_points_to_theirs(fixed, tmp_path):
    target = tmp_path / "D"
    target.mkdir()
    (target / "config.json").write_text('{"output_dir": "theirs"}', encoding="utf-8")
    data_location.move(str(target), "use")
    assert (fixed / "config.json").exists()                                 # ours stays where it was
    assert config.load()["output_dir"] == "theirs"


def test_back_to_the_fixed_folder_deletes_the_location(fixed, tmp_path):
    data_location.move(str(tmp_path / "D"))
    data_location.move(str(fixed))
    assert not (fixed / "location.json").exists() and (fixed / "config.json").exists()
    assert config.user_dir() == fixed


@pytest.mark.parametrize("path, error", [("", "chemin complet"), ("relative/folder", "chemin complet")])
def test_refused_paths(fixed, path, error):
    assert error in data_location.analyze(path)["error"]


def test_a_file_is_not_a_folder(fixed, tmp_path):
    f = tmp_path / "f.txt"
    f.write_text("x", encoding="utf-8")
    assert "fichier" in data_location.analyze(str(f))["error"]


def test_same_folder_does_nothing(fixed):
    assert data_location.analyze(str(fixed))["same"]
    assert "déjà dans" in data_location.move(str(fixed))


def test_routes_read_check_and_move(fixed, tmp_path):
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer

    from subtitles.web import server

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"

    def js(path, body=None):
        req = urllib.request.Request(base + path, method="POST" if body is not None else "GET", headers={"X-Language": "fr"},
                                     data=None if body is None else json.dumps(body).encode())
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    try:
        assert js("/api/data-location") == {"current": str(fixed), "fixed": str(fixed)}
        assert "chemin complet" in js("/api/data-location/check", {"path": "relative"})["error"]
        d = js("/api/data-location", {"path": str(tmp_path / "D"), "action": "move"})
        assert d["current"] == str(tmp_path / "D") and (tmp_path / "D" / "config.json").exists()
    finally:
        httpd.shutdown()
        httpd.server_close()
