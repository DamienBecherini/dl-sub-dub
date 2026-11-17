"""The MCP server: the protocol (initialize, tools/list, tools/call, notifications, errors) and its tools over the local
API, the service replaced by a fake; a stopped service said plainly."""
import io
import json
import sys

import pytest

from subtitles import mcp_server as m

PROJECT = {"id": "p1", "title": "Léa Parker", "root": "D:/V/test1", "state": "suspended", "stages": ["subtitles", "dubbing"],
           "order": "steps", "items": [{"id": "a", "source": "D:/V/test1/e8.mp4", "title": "", "video": "D:/V/test1/e8.mp4",
                                        "selected": True, "facts": {"duration": 2802.4, "detected_language": "fr"},
                                        "stages": {"download": {"state": "skipped"}, "subtitles": {"state": "done"},
                                                   "dubbing": {"state": "error", "error": "no voice"}}}]}


@pytest.fixture
def api(monkeypatch):
    seen = []

    def fake(method, path, body=None):
        seen.append((method, path, body))
        if path == "/api/sources":
            return {"items": [{"source": "https://youtu.be/x", "title": "X", "duration": 60, "kind": "address"}],
                    "errors": [{"entry": "D:/nope.mp4", "error": "not found"}]}
        if path == "/api/projects" and method == "GET":
            return [PROJECT]
        return PROJECT
    monkeypatch.setattr(m, "call", fake)
    return seen


def rpc(method, params=None, mid=1):
    return m.handle({"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}})


def tool(name, **args):
    r = rpc("tools/call", {"name": name, "arguments": args})["result"]
    return r["isError"], (r["content"][0]["text"] if r["isError"] else json.loads(r["content"][0]["text"]))


def test_initialize_and_the_list_of_tools():
    r = rpc("initialize", {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t"}})["result"]
    assert r["protocolVersion"] == "2025-03-26" and r["capabilities"] == {"tools": {}} and r["serverInfo"]["name"] == "dl-sub-dub"
    assert rpc("initialize", {"protocolVersion": "1999-01-01"})["result"]["protocolVersion"] == m.PROTOCOL
    tools = {t["name"]: t for t in rpc("tools/list")["result"]["tools"]}
    assert {"new_project", "project_status", "run_project", "run_video", "approve_translation"} <= set(tools)
    assert tools["project_status"]["annotations"]["readOnlyHint"] and not tools["run_video"]["annotations"]["readOnlyHint"]
    assert "right" in tools["new_project"]["description"]                       # the rights, said where it matters
    assert m.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert rpc("nope")["error"]["code"] == -32601
    assert rpc("tools/call", {"name": "nope"})["error"]["code"] == -32602


def test_the_project_in_short(api):
    err, p = tool("project_status", project_id="p1")
    assert not err and api[-1] == ("GET", "/api/projects/p1", None)
    v = p["videos"][0]
    assert v["title"] == "e8.mp4" and v["steps"] == {"subtitles": "done", "dubbing": "error: no voice"}   # no skipped step
    assert v["duration_s"] == 2802 and v["spoken_language"] == "fr"
    err, ps = tool("list_projects")
    assert ps[0]["id"] == "p1" and "videos" not in ps[0]


def test_a_new_project_from_addresses(api):
    err, p = tool("new_project", sources=["https://youtu.be/x", "D:/nope.mp4"], steps=["download", "subtitles"], title="T")
    assert not err and p["refused"] == [{"entry": "D:/nope.mp4", "error": "not found"}]
    method, path, body = api[-1]
    assert (method, path) == ("POST", "/api/projects") and body["stages"] == ["download", "subtitles"] and body["start"] is False
    assert body["items"] == [{"source": "https://youtu.be/x", "title": "X", "duration": 60}] and body["title"] == "T"


def test_steps_checked_and_one_video_run(api):
    err, text = tool("run_video", project_id="p1", video_id="a", steps=["subtitle"])
    assert err and "Unknown step" in text
    err, _ = tool("run_video", project_id="p1", video_id="a", steps=["subtitles", "dubbing"])
    assert not err and api[-1] == ("POST", "/api/projects/p1/items/a/run", {"stages": ["subtitles", "dubbing"]})
    err, text = tool("run_project")
    assert err and "project_id" in text
    tool("pause_project", project_id="p1", now=True)
    assert api[-1][1] == "/api/projects/p1/stop"


def test_a_stopped_service_said_plainly(monkeypatch):
    monkeypatch.setenv("DL_SUB_DUB_URL", "http://127.0.0.1:9")            # nothing listens there
    err, text = tool("service_status")
    assert err and "does not answer" in text and "127.0.0.1:9" in text


def test_stdio_one_message_a_line(monkeypatch, api):
    lines = [json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
             json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}), "not json",
             json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "list_projects", "arguments": {}}})]
    stdin = io.TextIOWrapper(io.BytesIO(("\n".join(lines) + "\n").encode()), encoding="utf-8")
    raw = io.BytesIO()
    stdout = io.TextIOWrapper(raw, encoding="utf-8")
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", io.TextIOWrapper(io.BytesIO(), encoding="utf-8"))
    m.main()
    stdout.flush()
    answers = [json.loads(x) for x in raw.getvalue().decode().splitlines()]
    assert [a.get("id") for a in answers] == [1, None, 2]                  # the notification has no answer
    assert answers[1]["error"]["code"] == -32700 and "Léa Parker" in answers[2]["result"]["content"][0]["text"]


# --- the review by the assistant -------------------------------------------------------------------------------------

SUBTITLES = {"item": "a", "title": "e8.mp4", "spoken_language": "fr", "language": "fr", "count": 3, "waiting": 1,
             "lines": [{"line": 1, "start": 0.0, "end": 2.0, "text": "Bonjour à tous."}, {"line": 2, "start": 62.5, "end": 64.0, "text": "Le bois de vingt scènes."},
                       {"line": 3, "start": 3725.0, "end": 3727.0, "text": "À bientôt."}]}


def reviewing(monkeypatch, answers):
    seen = []

    def fake(method, path, body=None):
        seen.append((method, path, body))
        return answers[path.split("?")[0].rsplit("/", 1)[-1]]
    monkeypatch.setattr(m, "call", fake)
    return seen


def test_the_subtitles_of_a_video_numbered_with_their_times(monkeypatch):
    seen = reviewing(monkeypatch, {"subtitles": SUBTITLES})
    err, r = tool("get_subtitles", project_id="p1", video_id="a")
    assert not err and seen[-1] == ("GET", "/api/projects/p1/items/a/subtitles", None)
    assert r["subtitles"].splitlines() == ["1 [0:00] Bonjour à tous.", "2 [1:02] Le bois de vingt scènes.", "3 [1:02:05] À bientôt."]
    assert (r["video"], r["spoken_language"], r["lines_in_all"], r["fixes_waiting_for_the_user"]) == ("e8.mp4", "fr", 3, 1)
    err, r = tool("get_subtitles", project_id="p1", video_id="a", first=2, last=2)
    assert r["subtitles"] == "2 [1:02] Le bois de vingt scènes." and (r["first"], r["last"]) == (2, 2)
    assert tool("get_subtitles", project_id="p1", video_id="a", first="x")[0]


def test_a_translation_is_read_with_its_original(monkeypatch):
    lines = [{**x, "translation": t} for x, t in zip(SUBTITLES["lines"], ["Hello everyone.", "The wood of twenty scenes.", "See you soon."])]
    seen = reviewing(monkeypatch, {"subtitles": {**SUBTITLES, "language": "en", "lines": lines, "stale": [2]}})
    err, r = tool("get_subtitles", project_id="p1", video_id="a", language="EN")
    assert not err and seen[-1][1] == "/api/projects/p1/items/a/subtitles?language=en"
    assert r["subtitles"].splitlines()[:2] == ["1 [0:00] Hello everyone.", "    original: Bonjour à tous."]
    assert r["language"] == "en" and "2" in r["note"] and "again" in r["note"]


def test_fixes_are_proposed_and_left_to_the_user(monkeypatch):
    answer = {"proposed": 1, "refused": [{"line": 3, "why": "unchanged"}, {"line": 9, "why": "unknown_line"}], "waiting": 2, "state": "done"}
    seen = reviewing(monkeypatch, {"corrections": answer})
    fixes = [{"line": 2, "corrected": "Le bois de Vincennes.", "reason": "parc cité ensuite", "confidence": "high"}, {"line": 3, "corrected": "à bientôt"},
             {"line": 9, "corrected": "x"}]
    err, r = tool("propose_corrections", project_id="p1", video_id="a", corrections=fixes)
    assert not err and seen[-1] == ("POST", "/api/projects/p1/items/a/corrections", {"corrections": fixes, "by": "assistant (MCP)", "complete": True})
    assert r["proposed"] == 1 and r["waiting_for_the_user"] == 2 and [x["line"] for x in r["refused"]] == [3, 9]
    assert "punctuation" in r["refused"][0]["why"] and "no such line" in r["refused"][1]["why"]
    assert "Nothing is applied yet" in r["next"] and "run_video" in r["next"]                # its subtitles are made: to run again
    reviewing(monkeypatch, {"corrections": {**answer, "state": "paused"}})
    assert "approve_translation" in tool("propose_corrections", project_id="p1", video_id="a", corrections=fixes, whole_video_read=False)[1]["next"]
    assert tool("propose_corrections", project_id="p1", video_id="a", corrections="x")[0]
    tools = {t["name"]: t for t in rpc("tools/list")["result"]["tools"]}
    assert tools["get_subtitles"]["annotations"]["readOnlyHint"] and not tools["propose_corrections"]["annotations"]["readOnlyHint"]
    assert "the user accepts" in tools["propose_corrections"]["description"]


def test_what_waits_for_the_user(monkeypatch):
    seen = reviewing(monkeypatch, {"review": {"proposals": [{"transcribed": "Rénal", "spelling": "Renan"}],
                                              "corrections": [{"item": "a", "title": "e8.mp4", "corrections": [{"id": "c62500", "line": 2}]}]}})
    err, r = tool("pending_corrections", project_id="p1")
    assert not err and seen[-1] == ("GET", "/api/projects/p1/review", None)
    assert r["names"][0]["transcribed"] == "Rénal" and r["lines"] == [{"video_id": "a", "video": "e8.mp4", "fixes": [{"id": "c62500", "line": 2}]}]
