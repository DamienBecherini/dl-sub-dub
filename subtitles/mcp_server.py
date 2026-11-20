"""An MCP server (Model Context Protocol) over the local API: an AI assistant without a terminal (Claude Desktop, a voice
assistant…) makes projects, runs their steps and follows them, as the page does. It can also review a video: read its
subtitles and propose the lines to fix, which the user decides on the page.

    python -m subtitles.mcp_server            # stdio; the web service must run (python -m subtitles.web)

Standard library only, like the rest of the service: JSON-RPC 2.0, one message per line on stdin and stdout (the stdio
transport of MCP); only the tools part of the protocol. It talks to the service at DL_SUB_DUB_URL (by default
http://127.0.0.1:4420, or the port of the settings), so the page and the assistant see the same projects.
"""
import json
import os
import sys
import urllib.error
import urllib.parse
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


def asks(p: dict) -> bool:
    """The project asks for the user's go-ahead: stopped for it, or not running with a video that waits (run on its own)."""
    return p.get("state") == "awaiting_translation" or bool((p.get("rules") or {}).get("go_ahead"))


def project_brief(p: dict, videos: bool = True) -> dict:
    out = {"id": p["id"], "title": p.get("title"), "folder": p.get("root"), "state": p.get("state"),
           "steps": p.get("stages"), "order": p.get("order"), "videos_count": len(p.get("items") or [])}
    if p.get("remaining_s"):
        out["remaining_s"] = p["remaining_s"]
    if asks(p):
        out["note"] = ("The project waits for the user's go-ahead: an AI review or a translation that may cost (see the estimate, then "
                       "approve_translation), fixes proposed by the AI review, to decide on the page first, or a video whose "
                       "transcription looks little like its language (`doubts` of the estimate: the engine probably does not "
                       "know the language spoken; tell the user before approving, the translation would be worthless).")
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
    if asks(p):
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


# --- the review by the assistant: the subtitles read, the lines to fix proposed (decided by the user on the page) ---
WHY_REFUSED = {"no_line": "no line number", "unknown_line": "no such line in this video", "twice": "proposed twice in this call",
               "unchanged": "nothing changed but punctuation, capitals or hyphens", "too_long": "much longer than the line (lines merged?)",
               "decided": "the user already decided on this line"}


def clock(seconds) -> str:
    s = int(seconds or 0)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def _video_path(a: dict, what: str) -> str:
    return f"/api/projects/{urllib.parse.quote(str(a['project_id']))}/items/{urllib.parse.quote(str(a['video_id']))}/{what}"


def t_get_subtitles(a):
    wanted = str(a.get("language") or "").strip().lower()
    r = call("GET", _video_path(a, "subtitles") + (f"?language={urllib.parse.quote(wanted)}" if wanted else ""))
    lines = r.get("lines") or []
    try:
        first, last = max(1, int(a.get("first") or 1)), min(len(lines), int(a.get("last") or len(lines)))
    except (TypeError, ValueError):
        raise ToolError("first and last are line numbers.") from None
    text = "\n".join(f"{x['line']} [{clock(x['start'])}] {x.get('translation', x['text'])}"
                     + (f"\n    original: {x['text']}" if "translation" in x else "") for x in lines[first - 1:last])
    out = {"video": r.get("title"), "spoken_language": r.get("spoken_language"), "language": r.get("language"), "lines_in_all": r.get("count"),
           "first": first, "last": last, "fixes_waiting_for_the_user": r.get("waiting"), "subtitles": text}
    if r.get("stale"):
        out["note"] = ("These lines were translated before their original was fixed (run the subtitles step of the video again to "
                       f"translate them again): {', '.join(str(n) for n in r['stale'])}.")
    return out


def t_propose_corrections(a):
    found = a.get("corrections")
    if not isinstance(found, list):
        raise ToolError("corrections: a list of {line, corrected, reason, confidence}.")
    r = call("POST", _video_path(a, "corrections"), {"corrections": found, "by": str(a.get("by") or "assistant (MCP)"),
                                                     "complete": a.get("whole_video_read") is not False})
    if r.get("state") == "done":
        then = ("Its subtitles are already made: once the user has decided, run_video with steps [\"subtitles\"] applies the "
                "accepted fixes (the subtitles are cut again, the changed lines translated again; a paid translator asks first).")
    elif r.get("state") == "paused":
        then = "The video waits for a go-ahead: once the user has decided, approve_translation goes on with the accepted fixes."
    else:
        then = "The accepted fixes apply when the subtitles step of the video runs."
    return {"proposed": r.get("proposed"),
            "refused": [{"line": x.get("line"), "why": WHY_REFUSED.get(x.get("why"), x.get("why"))} for x in r.get("refused") or []],
            "waiting_for_the_user": r.get("waiting"),
            "next": "Nothing is applied yet: the user accepts, retouches or refuses each line on the page of the project. " + then}


def t_pending_corrections(a):
    r = call("GET", f"/api/projects/{urllib.parse.quote(str(a['project_id']))}/review")
    return {"names": r.get("proposals") or [],
            "lines": [{"video_id": v.get("item"), "video": v.get("title"), "fixes": v.get("corrections")} for v in r.get("corrections") or []]}


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
    "get_subtitles": (t_get_subtitles, "The subtitles of one video already transcribed, numbered, with the time each starts "
                      "at: in the spoken language, or with language (e.g. \"en\") its translation with the original under each "
                      "line. To review them (propose_corrections uses the same numbers) or to judge a translation. A long video "
                      "can be read in parts with first and last.",
                      {**PROJECT, **VIDEO, "language": {"type": "string", "description": "Code of a translation to read (default: the spoken language)."},
                       "first": {"type": "integer", "description": "First line to give (default 1)."},
                       "last": {"type": "integer", "description": "Last line to give (default: the last one)."}},
                      ["project_id", "video_id"], True),
    "propose_corrections": (t_propose_corrections, "Proposes lines to fix in the subtitles of a video, in its spoken language, "
                            "after reading them with get_subtitles (same line numbers). Only mistakes of the automatic "
                            "transcription: a word or a name heard wrong that the context settles, a number or a date that "
                            "another line contradicts, a sentence never said (corrected \"\" removes it). Give the whole line "
                            "corrected, the rest word for word: no restyling, no fix of spoken grammar or of punctuation alone, "
                            "no line merged or moved, nothing translated; when in doubt, confidence low or nothing. Nothing is "
                            "applied by this call: the user accepts, retouches or refuses each line on the page.",
                            {**PROJECT, **VIDEO,
                             "corrections": {"type": "array", "description": "The lines to fix.", "items": {
                                 "type": "object", "required": ["line", "corrected", "reason"], "additionalProperties": False,
                                 "properties": {"line": {"type": "integer", "description": "Its number in get_subtitles."},
                                                "corrected": {"type": "string", "description": "The whole line, corrected (\"\": never said, to remove)."},
                                                "reason": {"type": "string", "description": "One short sentence, in the user's language."},
                                                "confidence": {"type": "string", "enum": ["high", "medium", "low"]}}}},
                             "whole_video_read": {"type": "boolean", "description": "True (default) when every subtitle of the video "
                                                  "was read: the AI review of the project will not read it again. False for a part only."}},
                            ["project_id", "video_id", "corrections"], False),
    "pending_corrections": (t_pending_corrections, "What the review of a project proposes and the user has not decided yet: "
                            "the names heard wrong, and video by video the lines to fix (from the AI review or from "
                            "propose_corrections).", PROJECT, ["project_id"], True),
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
                                      "with the original voice cloned. The web service must run. You can also review a "
                                      "video yourself: get_subtitles, then propose_corrections; the user decides on the "
                                      "page. " + RIGHTS}
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
