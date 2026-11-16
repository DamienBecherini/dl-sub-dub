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
