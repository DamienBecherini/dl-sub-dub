"""Routes of the web server, through real HTTP requests (configuration and saving replaced)."""
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from subtitles.web.routes import videos as video_routes
from subtitles.web import router
from subtitles.web.routes import projects as project_routes
from subtitles.web.routes import pages as page_routes
from subtitles import config
from subtitles.web import server


@pytest.fixture
def base(tmp_path, monkeypatch):
    cfg = {"output_dir": str(tmp_path / "out"), "transcriptions": [], "translators": [], "ui_language": "fr"}
    saved = []
    monkeypatch.setattr(config, "load", lambda *_a, **_k: json.loads(json.dumps(cfg)))
    monkeypatch.setattr(config, "save", lambda c, *_a, **_k: saved.append(c))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", saved
    httpd.shutdown()
    httpd.server_close()


def call(url, method="GET", body=None, raw=None, headers=None):
    data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
    req = urllib.request.Request(url, data=data, method=method, headers={"X-Language": "fr", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.headers.get("Content-Type"), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type"), e.read()


def js(url, method="GET", body=None, raw=None, headers=None):
    code, _type, data = call(url, method, body, raw, headers)
    return code, json.loads(data)


def test_the_connections_of_a_page_wait_for_their_turn():
    """The page asks for its files and its data all at once, each on its own connection: a server that is busy for a
    moment (here, one that accepts nothing) must keep them waiting, not refuse them."""
    import socket
    httpd = server.Server(("127.0.0.1", 0), server.Handler)            # listening, never accepting
    opened = []
    try:
        for _ in range(40):
            opened.append(socket.create_connection(httpd.server_address, timeout=2))
    finally:
        for s in opened:
            s.close()
        httpd.server_close()
    assert len(opened) == 40


def test_page_static_files_and_catalogs(base):
    url, _ = base
    code, ctype, body = call(url + "/")
    assert code == 200 and ctype.startswith("text/html") and b"<html" in body.lower()
    assert js(url + "/static/missing.js")[0] == 404
    assert js(url + "/static/../server.py")[0] == 404
    code, ctype, body = call(url + "/locales/fr.json")
    assert code == 200 and ctype.startswith("application/json") and json.loads(body)
    assert js(url + "/locales/xx.json")[0] == 404


def test_read_routes(base):
    url, _ = base
    code, d = js(url + "/api/health")
    assert code == 200 and d["service"] == "subtitles" and d["contract"] == 2
    code, d = js(url + "/api/languages")
    assert code == 200 and "fr" in d["available"] and d["setting"] == "fr"
    code, d = js(url + "/api/status")
    assert code == 200 and d["local_ai"] is None and "task" in d
    assert js(url + "/api/config")[1]["ui_language"] == "fr"
    code, d = js(url + "/api/tasks")
    assert code == 200 and isinstance(d, list)
    assert js(url + "/api/projects")[0] == 200
    assert "last" in js(url + "/api/projects/last")[1]
    code, d = js(url + "/api/cleanup")
    assert code == 200 and d["root"].endswith(".work") and "options" in d


def test_unknown_identifiers_and_paths(base):
    url, _ = base
    for path, message in (("/api/tasks/ab12", "tâche"), ("/api/tasks/ab12/estimate", None), ("/api/projects/ab12", "projet"),
                          ("/api/projects/ab12/estimate", "projet"), ("/api/projects/ab12/review", "projet"),
                          ("/api/folder/ab12", None), ("/api/nothing", None), ("/api/tasks/XYZ", None)):
        code, d = js(url + path)
        assert code == 404 and d["error"], path
        if message:
            assert message in d["error"].lower(), (path, d)
    assert js(url + "/api/nothing", "POST", {})[0] == 404
    assert js(url + "/api/nothing", "PUT", {})[0] == 404
    assert js(url + "/api/health", "POST", {})[0] == 404


def test_actions_and_errors(base, tmp_path):
    url, saved = base
    code, d = js(url + "/api/tasks", "POST", {"source": "  "})
    assert code == 400 and d["error"]
    code, d = js(url + "/api/tasks", "POST", {"source": str(tmp_path)})
    assert code == 409 and d["folder"] is True
    assert js(url + "/api/tasks", "POST", raw=b"{not json")[0] == 400
    assert js(url + "/api/tasks/ab12/cancel", "POST", {}) == (200, {"ok": False})
    assert js(url + "/api/tasks/ab12/retry", "POST", {})[0] == 404
    assert js(url + "/api/tasks/ab12/translate", "POST", {})[0] == 409
    assert js(url + "/api/projects", "POST", {"items": []})[0] == 400
    video = tmp_path / "a.mp4"
    video.write_bytes(b"x")
    assert js(url + "/api/projects", "POST", {"items": [{"source": video.as_posix()}], "settings": {"nowhere": 1}})[0] == 400    # not a setting
    code, d = js(url + "/api/projects", "POST", {"items": [{"source": video.as_posix(), "language": "en"}], "stages": ["subtitles"],
                                                 "settings": {"languages": ["de"], "spend_agreed": True}})
    assert code == 201 and d["options"]["languages"] == ["de"] and d["options"]["spend_agreed"] is True and d["state"] == "suspended"
    assert d["items"][0]["overrides"] == {"language": "en"}
    assert js(url + f"/api/projects/{d['id']}")[1]["id"] == d["id"] and d["id"] in [p["id"] for p in js(url + "/api/projects")[1]]
    assert js(url + f"/api/projects/{d['id']}/cancel", "POST", {})[1]["state"] == "cancelled"
    for action in ("suspend", "resume", "cancel", "retry", "continue", "goal"):
        code, d = js(url + f"/api/projects/ab12/{action}", "POST", {})
        assert code == 409 and action in d["error"], action
    assert js(url + "/api/projects/ab12/translate", "POST", {"stop": "nowhere"})[0] == 400
    assert js(url + "/api/projects/ab12/validate", "POST", {"decisions": []})[0] == 404
    assert js(url + "/api/projects/load", "POST", {"file": str(tmp_path / "none.batch.json")})[0] == 400
    assert js(url + "/api/projects/last/forget", "POST", {}) == (200, {"ok": True})
    assert js(url + "/api/folder", "POST", {"path": str(tmp_path / "none")})[0] == 400
    assert js(url + "/api/folder/ab12/cancel", "POST", {}) == (200, {"ok": True})
    assert js(url + "/api/open", "POST", {"path": str(tmp_path / "none")})[0] == 404
    assert js(url + "/api/test", "POST", {"kind": "translator", "engine": {"type": "none"}})[1]["ok"] is True
    assert js(url + "/api/test", "POST", {"kind": "other"})[0] == 400
    assert js(url + "/api/local-transcription/stop", "POST", {}) == (200, {"ok": True})
    code, d = js(url + "/api/cleanup", "POST", {"options": {}})
    assert code == 200 and "deleted" in d
    code, d = js(url + "/api/config", "PUT", {"ui_language": "fr", "x": 1})
    assert code == 200 and saved == [{"ui_language": "fr", "x": 1}]


def test_upload_of_a_file(base, tmp_path):
    url, _ = base
    assert js(url + "/api/files", "PUT", raw=b"abc")[0] == 400
    code, d = js(url + "/api/files?name=sub%20t.srt", "PUT", raw=b"hello")
    assert code == 200 and d["existed"] is False and (tmp_path / "out" / "sub t.srt").read_bytes() == b"hello"
    assert js(url + "/api/files?name=sub%20t.srt", "PUT", raw=b"world")[1]["existed"] is True


def test_upload_never_overwrites_another_file(base, tmp_path):
    """A dropped file whose name is taken by a different file (another size) gets "name (2).ext": the other stays."""
    url, _ = base
    out = tmp_path / "out"
    assert js(url + "/api/files?name=clip.mp4", "PUT", raw=b"first")[0] == 200
    code, d = js(url + "/api/files?name=clip.mp4", "PUT", raw=b"second, longer")
    assert code == 200 and d["existed"] is False and d["path"] == str(out / "clip (2).mp4")
    assert (out / "clip.mp4").read_bytes() == b"first" and (out / "clip (2).mp4").read_bytes() == b"second, longer"
    assert js(url + "/api/files?name=clip.mp4", "PUT", raw=b"a third one")[1]["path"] == str(out / "clip (3).mp4")
    assert js(url + "/api/files?name=..%2F..%2Fescape.txt", "PUT", raw=b"x")[1]["path"] == str(out / "escape.txt")


def test_upload_needs_room_on_the_disk(base, tmp_path, monkeypatch):
    """Asked first (GET /api/files/room), then checked again on the copy: a disk too full refuses it (507), with the
    body read all the same (the answer arrives), and nothing written."""
    from collections import namedtuple
    from subtitles import space
    url, _ = base
    code, d = js(url + "/api/files/room?size=1000")
    assert code == 200 and d["enough"] is True and d["folder"] == str(tmp_path / "out") and d["free"] > 0
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(space.shutil, "disk_usage", lambda p: usage(10, 10, 100 * 2**20))      # 100 MB free
    code, d = js(url + "/api/files/room?size=1000")
    assert code == 200 and d["enough"] is False and d["error"]                                 # under the margin
    code, d = js(url + "/api/files?name=big.mp4", "PUT", raw=b"x" * 3_000_000)
    assert code == 507 and d["error"] and not (tmp_path / "out" / "big.mp4").exists()


def test_query_parameter_of_wrong_type_is_a_bad_request(base):
    url, _ = base
    assert js(url + "/api/tasks/ab12?since=abc")[0] == 400


def test_router_parameters_and_order():
    fn, params = router.resolve("GET", "/api/tasks/ab12")
    assert fn is video_routes.get_task and params == {"id": "ab12"}
    assert router.resolve("GET", "/api/projects/last")[0] is project_routes.get_last_project
    assert router.resolve("POST", "/api/projects/ab12/cancel") == (project_routes.suspend_or_cancel_project, {"id": "ab12", "action": "cancel"})
    assert router.resolve("POST", "/api/projects/ab12/explode") is None
    assert router.resolve("GET", "/api/tasks/AB12") is None          # identifiers are lowercase hexadecimal
    assert router.resolve("DELETE", "/api/tasks/ab12") is None
    assert router.resolve("GET", "/static/css/app.css") == (page_routes.static_file, {"name": "css/app.css"})
    routes = [(m, r.pattern) for m, r, _fn in router.ROUTES]
    assert len(routes) == len(set(routes)), "route declared twice"


def test_a_page_of_another_site_cannot_use_the_api(base, tmp_path):
    """CSRF: a site open in the browser posts to the API (text/plain: no preflight). Here, a command to run."""
    url, saved = base
    marker = tmp_path / "run.txt"
    engine = {"url": "http://127.0.0.1:9", "command": ["python", "-c", f"open(r'{marker}', 'w')"]}
    for origin in ("https://evil.example", "null", "http://127.0.0.1:1"):
        code, d = js(url + "/api/test", "POST", raw=json.dumps({"kind": "transcription", "engine": engine}).encode(),
                     headers={"Content-Type": "text/plain", "Origin": origin})
        assert code == 403 and "refusée" in d["error"], origin
    assert js(url + "/api/config", "PUT", {"x": 1}, headers={"Origin": "https://evil.example"})[0] == 403
    assert not marker.exists() and saved == []


def test_dns_rebinding_is_refused(base):
    """A site whose name now points to 127.0.0.1: same origin for the browser, but its name in Host."""
    url, _ = base
    assert js(url + "/api/config", headers={"Host": "evil.example"})[0] == 403
    assert js(url + "/api/tasks/ab12/cancel", "POST", {}, headers={"Host": "evil.example", "Origin": "http://evil.example"})[0] == 403


def test_sec_fetch_site_of_another_site_is_refused(base):
    """Second lock after Origin: the browser says who made the request. Another site, or another port of this PC, is
    refused (reading too); opening the page from a link stays possible; the page itself is accepted."""
    url, saved = base
    for site in ("cross-site", "same-site"):
        assert js(url + "/api/config", "PUT", {"x": 1}, headers={"Sec-Fetch-Site": site})[0] == 403, site
        assert js(url + "/api/config", headers={"Sec-Fetch-Site": site, "Sec-Fetch-Mode": "no-cors"})[0] == 403, site
        assert call(url + "/", headers={"Sec-Fetch-Site": site, "Sec-Fetch-Mode": "navigate"})[0] == 200, site
    assert saved == []
    for site in ("same-origin", "none"):
        assert js(url + "/api/tasks/ab12/cancel", "POST", {}, headers={"Sec-Fetch-Site": site}) == (200, {"ok": False}), site


def test_a_body_that_is_not_an_object_is_a_bad_request(base):
    """A list or a number where a route reads fields: 400 with a message, never a connection cut without an answer."""
    url, _ = base
    for raw in (b"[1, 2]", b"3", b'"text"', b"null"):
        code, d = js(url + "/api/tasks", "POST", raw=raw)
        assert code == 400 and d["error"], raw


def test_the_page_itself_and_other_clients_are_accepted(base):
    url, _ = base
    port = url.rsplit(":", 1)[1]
    for headers in ({"Origin": url}, {"Origin": f"http://localhost:{port}", "Host": f"localhost:{port}"}, {}):
        assert js(url + "/api/tasks/ab12/cancel", "POST", {}, headers=headers) == (200, {"ok": False}), headers


def test_playlist_routes(base, monkeypatch):
    url, _ = base
    assert js(url + "/api/playlist", "POST", {"url": "not an address"})[0] == 400
    assert js(url + "/api/playlist/ab12")[0] == 404
    assert js(url + "/api/playlist/ab12/cancel", "POST", {}) == (200, {"ok": True})
    entries = [{"url": "https://v.example/a", "title": "A", "duration": 60}, {"url": "https://v.example/b", "title": "B", "duration": None}]
    monkeypatch.setattr(video_routes.source, "playlist_info", lambda *_a: {"id": "PL", "title": "Course: 1", "entries": entries, "with_video": False})
    code, d = js(url + "/api/youtube", "POST", {"url": "https://v.example/list"})
    assert code == 200 and d["ok"] and "title" not in d
    assert d["playlist"]["count"] == 2 and d["playlist"]["duration"] == 60 and d["playlist"]["folder"].endswith("/Course 1")


def test_list_of_addresses_routes(base, tmp_path, monkeypatch):
    url, _ = base
    f = tmp_path / "Courses.txt"
    f.write_text("https://v.example/a\nsecret line of another file\n# comment\nhttps://v.example/b\n", encoding="utf-8")
    code, d = js(url + "/api/addresses", "POST", {"path": str(f)})
    assert code == 200 and d == {"addresses": ["https://v.example/a", "https://v.example/b"], "title": "Courses", "ignored": 1}
    assert js(url + "/api/addresses", "POST", {"path": str(tmp_path / "missing.txt")})[0] == 400
    assert js(url + "/api/playlist", "POST", {"urls": ["https://v.example/a", "D:/video.mp4"]})[0] == 400
    assert js(url + "/api/playlist", "POST", {"urls": []})[0] == 400
    started = []
    monkeypatch.setattr(video_routes.PlaylistDownload, "start", lambda self: started.append((self.urls, self.title)))
    code, d = js(url + "/api/playlist", "POST", {"urls": ["https://v.example/a", "https://v.example/b"], "title": "My: list"})
    assert code == 202 and started == [(["https://v.example/a", "https://v.example/b"], "My list")]
