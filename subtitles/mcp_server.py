"""An MCP server (Model Context Protocol) over the local API: an AI assistant without a terminal (Claude Desktop, a voice
assistant…) makes projects, runs their steps and follows them, as the page does.

    python -m subtitles.mcp_server            # stdio; the web service must run (python -m subtitles.web)

Standard library only, like the rest of the service: JSON-RPC 2.0, one message per line on stdin and stdout (the stdio
transport of MCP); only the tools part of the protocol. It talks to the service at DL_SUB_DUB_URL (by default
http://127.0.0.1:4420, or the port of the settings), so the page and the assistant see the same projects.
"""
import json
import os
import sys
import urllib.error
import urllib.request

from . import __version__, config

PROTOCOL = "2025-06-18"                        # answered when the client asks for none we know
KNOWN = ("2024-11-05", "2025-03-26", "2025-06-18")
STEPS = ["download", "subtitles", "dubbing"]
RIGHTS = ("Only for videos the user has the right to use; the dubbing clones the original voice: only with the right "
          "to use that voice.")


class ToolError(Exception):
    """Said to the assistant as the result of its call (isError), not as a protocol error."""


def base_url() -> str:
    if os.environ.get("DL_SUB_DUB_URL"):
        return os.environ["DL_SUB_DUB_URL"].rstrip("/")
    try:
        port = int(config.load().get("web", {}).get("port", 4420))
    except Exception:                                    # noqa: BLE001 (settings unreadable: the default port)
        port = 4420
    return f"http://127.0.0.1:{port}"


def call(method: str, path: str, body: dict | None = None):
    """One request to the service → its JSON; its error message, or "not running", as a ToolError."""
    url = base_url() + path
    req = urllib.request.Request(url, method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "X-Language": "en"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read() or b"{}").get("error") or e.reason
        except ValueError:
            detail = e.reason
        raise ToolError(f"{detail} (HTTP {e.code})") from None
    except (urllib.error.URLError, OSError):
        raise ToolError(f"The dl-sub-dub service does not answer at {base_url()}: it is stopped or restarting. "
                        "Start it (python -m subtitles.web), then try again.") from None


# --- what the assistant reads: the project in short ---
def video_brief(item: dict) -> dict:
    steps = {}
    for k, st in (item.get("stages") or {}).items():
        if st.get("state") == "skipped":
            continue
        steps[k] = st["state"] + (f": {st['error']}" if st.get("error") else "")
    facts = item.get("facts") or {}
    return {"id": item["id"], "title": item.get("title") or (item.get("video") or item["source"]).replace("\\", "/").split("/")[-1],
            "source": item["source"], "ticked": item.get("selected", True), "steps": steps,
            **({"duration_s": round(facts["duration"])} if facts.get("duration") else {}),
            **({"spoken_language": facts["detected_language"]} if facts.get("detected_language") else {})}


def project_brief(p: dict, videos: bool = True) -> dict:
    out = {"id": p["id"], "title": p.get("title"), "folder": p.get("root"), "state": p.get("state"),
           "steps": p.get("stages"), "order": p.get("order"), "videos_count": len(p.get("items") or [])}
    if p.get("remaining_s"):
        out["remaining_s"] = p["remaining_s"]
    if p.get("state") == "awaiting_translation":
        out["note"] = "The translation waits for the user's go-ahead (it may cost): see the estimate, then approve_translation."
    if videos:
        out["videos"] = [video_brief(i) for i in p.get("items") or []]
    return out


def steps_of(names) -> list[str] | None:
    if names is None:
        return None
    names = [str(n).strip().lower() for n in names]
    bad = [n for n in names if n not in STEPS]
    if bad:
        raise ToolError(f"Unknown step(s): {', '.join(bad)}. The steps: download, subtitles, dubbing.")
    return names


def resolved(sources: list[str]) -> tuple[list[dict], list[dict]]:
    """Addresses, files, folders, .txt lists → the videos they hold (nothing downloaded), and the entries refused."""
    entries = [str(s).strip() for s in sources or [] if str(s).strip()]
    if not entries:
        raise ToolError("No video given: addresses, paths of video files, folders or .txt lists.")
    r = call("POST", "/api/sources", {"entries": entries})
    return r.get("items") or [], r.get("errors") or []


# --- the tools ---
def t_service_status(a):
    h = call("GET", "/api/health")
    return {"service": base_url(), "busy": h.get("busy"), "activity": h.get("activity"),
            "remaining_work_s": h.get("remaining_work_s"), "version": h.get("version")}


def t_list_projects(a):
    return [project_brief(p, videos=False) for p in call("GET", "/api/projects")]


def t_project_status(a):
    p = call("GET", f"/api/projects/{a['project_id']}")
    out = project_brief(p)
    if p.get("state") == "awaiting_translation":
        try:
            out["translation_estimate"] = call("GET", f"/api/projects/{a['project_id']}/estimate")
        except ToolError:
            pass
    return out


def t_new_project(a):
    items, errors = resolved(a.get("sources"))
    if not items:
        raise ToolError("Nothing to add: " + "; ".join(f"{e.get('entry')}: {e.get('error')}" for e in errors))
    body = {"items": [{k: i[k] for k in ("source", "title", "duration") if i.get(k)} for i in items],
            "start": bool(a.get("start"))}
    if a.get("title"):
        body["title"] = a["title"]
    if a.get("steps") is not None:
        body["stages"] = steps_of(a["steps"])
    if a.get("folder"):
        body["location"] = {"base": a["folder"], "named": bool(a.get("subfolder", True))}
    p = call("POST", "/api/projects", body)
    return {**project_brief(p), **({"refused": errors} if errors else {})}


def t_add_videos(a):
    items, errors = resolved(a.get("sources"))
    r = call("POST", f"/api/projects/{a['project_id']}/items",
             {"items": [{k: i[k] for k in ("source", "title", "duration") if i.get(k)} for i in items]})
    return {"added": r.get("added"), "project": project_brief(r["project"]), **({"refused": errors} if errors else {})}


def t_open_project(a):
    return project_brief(call("POST", "/api/projects/load", {"file": a["file"]}))


def t_run_project(a):
    body = {}
    if a.get("steps") is not None:
        body = {"stages": steps_of(a["steps"]), "only": bool(a.get("only"))}
    return project_brief(call("POST", f"/api/projects/{a['project_id']}/start", body))


def t_run_video(a):
    steps = steps_of(a.get("steps") or [])
    if not steps:
        raise ToolError("Give the steps to run for this video: download, subtitles, dubbing (in that order).")
    return project_brief(call("POST", f"/api/projects/{a['project_id']}/items/{a['video_id']}/run", {"stages": steps}))


def t_cancel_video(a):
    step = steps_of([a["step"]])[0]
    return project_brief(call("POST", f"/api/projects/{a['project_id']}/items/{a['video_id']}/cancel", {"stage": step}))


def t_approve_translation(a):
    return project_brief(call("POST", f"/api/projects/{a['project_id']}/translate", {}))


def t_pause_project(a):
    action = "stop" if a.get("now") else "suspend"
    return project_brief(call("POST", f"/api/projects/{a['project_id']}/{action}", {}))


PROJECT = {"project_id": {"type": "string", "description": "The id of the project (list_projects, new_project)."}}
VIDEO = {"video_id": {"type": "string", "description": "The id of a video of the project (project_status)."}}
STEP_LIST = {"type": "array", "items": {"type": "string", "enum": STEPS}}
TOOLS = {
    "service_status": (t_service_status, "Whether the dl-sub-dub service runs, and what it is doing.", {}, [], True),
    "list_projects": (t_list_projects, "The projects the service knows (opened since it started), newest first.", {}, [], True),
    "project_status": (t_project_status, "One project: its state, steps, time left, and each video with the state of "
                       "each step (todo, running, paused, done, error with its reason…). While a paid translation waits "
                       "for the go-ahead, its estimate (requests, tokens, price).", PROJECT, ["project_id"], True),
    "new_project": (t_new_project, "Makes a project from video addresses (YouTube, Dailymotion, PeerTube, Arte…, "
                    "playlists too), video files, folders or .txt lists of addresses. Steps: download (addresses only), "
                    "subtitles (transcription, translation into the project's languages), dubbing (needs a graphics card "
                    "and the dubbing set up). Not started unless start is true. " + RIGHTS,
                    {"sources": {"type": "array", "items": {"type": "string"}, "description": "Addresses or paths."},
                     "title": {"type": "string", "description": "Name of the project (default: from the videos)."},
                     "steps": {**STEP_LIST, "description": "Steps of the project (default: those of the settings)."},
                     "folder": {"type": "string", "description": "Absolute folder for the project (default: the download folder for addresses, the folder of the files otherwise)."},
                     "subfolder": {"type": "boolean", "description": "Add a subfolder named after the project in that folder (default true)."},
                     "start": {"type": "boolean", "description": "Run it at once (default false)."}},
                    ["sources"], False),
    "add_videos": (t_add_videos, "Adds videos (addresses, files, folders, .txt lists) to a project. " + RIGHTS,
                   {**PROJECT, "sources": {"type": "array", "items": {"type": "string"}}}, ["project_id", "sources"], False),
    "open_project": (t_open_project, "Opens a project file again (dl-sub-dub.project.json, in the project's folder), "
                     "suspended, to resume it with run_project.",
                     {"file": {"type": "string", "description": "Path of the dl-sub-dub.project.json."}}, ["file"], False),
    "run_project": (t_run_project, "Runs (or resumes) a project: the steps ticked for it, for the videos ticked. With "
                    "steps and only=true, those steps alone without changing the project's. A paid translation stops "
                    "first to show its cost (project_status, then approve_translation). " + RIGHTS,
                    {**PROJECT, "steps": STEP_LIST, "only": {"type": "boolean"}}, ["project_id"], False),
    "run_video": (t_run_video, "Runs steps for one video only, one after the other (e.g. [\"subtitles\", \"dubbing\"]): "
                  "each starts when the one before ends; an error stops the rest. Also Retry after an error. " + RIGHTS,
                  {**PROJECT, **VIDEO, "steps": STEP_LIST}, ["project_id", "video_id", "steps"], False),
    "cancel_video": (t_cancel_video, "Cancels one step of one video (its download, subtitles or dubbing).",
                     {**PROJECT, **VIDEO, "step": {"type": "string", "enum": STEPS}}, ["project_id", "video_id", "step"], False),
    "approve_translation": (t_approve_translation, "Gives the go-ahead to a translation that waits for it (it may cost "
                            "money: tell the user the estimate of project_status and get their agreement first).",
                            PROJECT, ["project_id"], False),
    "pause_project": (t_pause_project, "Pauses a project: the videos under way finish, then it waits (now=true: stops at "
                      "once, what is finished is kept and a download resumes where it stopped). run_project resumes it.",
                      {**PROJECT, "now": {"type": "boolean"}}, ["project_id"], False),
}


def tool_list() -> list[dict]:
    return [{"name": name, "description": desc,
             "inputSchema": {"type": "object", "properties": props, "required": req, "additionalProperties": False},
             "annotations": {"readOnlyHint": ro, "openWorldHint": False}}
            for name, (_, desc, props, req, ro) in TOOLS.items()]


def call_tool(name: str, args: dict) -> dict:
    if name not in TOOLS:
        raise KeyError(name)
    fn, _, _, required, _ = TOOLS[name]
    try:
        missing = [k for k in required if args.get(k) in (None, "", [])]
        if missing:
            raise ToolError(f"Missing: {', '.join(missing)}.")
        result = fn(args)
        return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=1)}], "isError": False}
    except ToolError as e:
        return {"content": [{"type": "text", "text": str(e)}], "isError": True}


def handle(msg: dict) -> dict | None:
    """One JSON-RPC message → its answer (None for a notification)."""
    mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
    if mid is None:                                      # notifications (initialized, cancelled…): nothing to answer
        return None
    try:
        if method == "initialize":
            asked = params.get("protocolVersion")
            result = {"protocolVersion": asked if asked in KNOWN else PROTOCOL, "capabilities": {"tools": {}},
                      "serverInfo": {"name": "dl-sub-dub", "version": __version__},
                      "instructions": "dl-sub-dub downloads videos, subtitles them in the languages wanted and dubs them "
                                      "with the original voice cloned. The web service must run. " + RIGHTS}
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": tool_list()}
        elif method == "tools/call":
            result = call_tool(params.get("name"), params.get("arguments") or {})
        else:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Method not found: {method}"}}
    except KeyError as e:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": f"Unknown tool: {e.args[0]}"}}
    except Exception as e:                               # noqa: BLE001 (any bug: an answer, never a dead server)
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": f"{type(e).__name__}: {e}"}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def main():
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")             # the protocol is UTF-8, whatever the console's code page
    out = sys.stdout
    sys.stdout = sys.stderr                              # nothing but protocol on stdout
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            answer = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        else:
            answer = handle(msg) if isinstance(msg, dict) else {"jsonrpc": "2.0", "id": None,
                                                                "error": {"code": -32600, "message": "Invalid request"}}
        if answer is not None:
            out.write(json.dumps(answer, ensure_ascii=False) + "\n")
            out.flush()


if __name__ == "__main__":
    main()
