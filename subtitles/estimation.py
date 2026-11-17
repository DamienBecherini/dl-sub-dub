"""Cost estimate of a translation before running it: requests, tokens, price per Claude model, duration locally.

Constants in `[estimation]` of the configuration. Values measured on 08/11/2026 on two 30 min videos translated
end to end (494 and 562 English subtitles, chunks of 25 lines, Sonnet 5.5 effort low): 36 input tokens and 23 output
tokens per subtitle; Qwen3.6-35B-A3B locally: 0.6 s per subtitle (331 lines in 197 s). Prices in dollars per million
tokens (input, output), Anthropic table of 06/10/2026.
"""
import math

from . import config, corrections, i18n

DEFAULTS = {
    "input_tokens_per_line": 36,
    "output_tokens_per_line": 23,
    "local_seconds_per_line": 0.6,
    # AI review (see `review`): measured 17/11/2026 on two videos (1112 subtitles, 161 names; Sonnet 5.5 effort medium: 0.11 $)
    "review_request_tokens": 700,              # the instructions, sent with each request
    "review_input_tokens_per_line": 24,        # full review: a numbered subtitle
    "review_output_tokens_per_line": 4,        # the corrections and the reasoning of the model, spread over every subtitle
    "review_input_tokens_per_name": 65,        # review of the names: a name with its two examples
    "review_output_tokens_per_name": 16,
    "review_local_seconds_per_line": 0.8,       # measured 17/11/2026: Qwen3.6-35B-A3B, 1112 subtitles in 850 s
    "review_local_seconds_per_name": 1.2,       # 161 names in 180 s
    "claude_code_request_tokens": 1000,         # what Claude Code adds to each request (17/11/2026, v2.1.326: about 650, 1300 with a JSON answer)
    "prices": {"claude-opus-5-5": [4.0, 20.0], "claude-sonnet-5-5": [2.0, 10.0], "claude-haiku-5-5": [0.10, 0.50]},
}


def settings(cfg: dict) -> dict:
    r = {**DEFAULTS, **(cfg.get("estimation") or {})}
    r["prices"] = {**DEFAULTS["prices"], **((cfg.get("estimation") or {}).get("prices") or {})}
    return r


def model_price(cfg: dict, model: str) -> tuple[float, float] | None:
    """(input, output) in $/MTok; a name without the exact version is matched by prefix (claude-sonnet-5-5-20261001…)."""
    table = settings(cfg)["prices"]
    if model in table:
        return tuple(table[model])
    for name, p in sorted(table.items(), key=lambda kv: -len(kv[0])):
        if model.startswith(name):
            return tuple(p)
    return None


def cost_usd(cfg: dict, model: str, input_tokens: int, output_tokens: int) -> float | None:
    p = model_price(cfg, model)
    if not p:
        return None
    return round(input_tokens * p[0] / 1e6 + output_tokens * p[1] / 1e6, 4)


def estimate(cfg: dict, line_count: int, languages: list[str], source_language: str) -> dict:
    """For `line_count` subtitles to translate into `languages` (the source language is skipped): requests, tokens
    and, for each configured translator, the cost (Claude) or the duration (local)."""
    r = settings(cfg)
    targets = [c for c in languages if c != source_language]
    chunk = max(1, int((cfg.get("translation") or {}).get("chunk_size", 25)))
    n = line_count * len(targets)
    e = {"count": line_count, "languages": targets, "requests": math.ceil(line_count / chunk) * len(targets),
         "input_tokens": round(n * float(r["input_tokens_per_line"])), "output_tokens": round(n * float(r["output_tokens_per_line"])),
         "translators": []}
    for m in cfg.get("translators", []):
        t = {"id": m.get("id"), "name": config.engine_name(m), "type": m.get("type", "openai"), "model": m.get("model", "")}
        if t["type"] == "claude":
            t["cost_usd"] = cost_usd(cfg, t["model"], e["input_tokens"], e["output_tokens"])
        elif t["type"] == "claude_code":             # on the subscription of the user: nothing billed, tokens of its limits
            t.update(cost_usd=0.0, subscription=True,
                     tokens=e["input_tokens"] + e["output_tokens"] + round(e["requests"] * float(r["claude_code_request_tokens"])))
        elif t["type"] == "openai":
            t["duration_s"] = round(n * float(r["local_seconds_per_line"]))
            t["cost_usd"] = 0.0 if is_local(m) else None
        else:
            t["cost_usd"] = 0.0
        e["translators"].append(t)
    return e


# Output of a full review by Claude at each effort, against the medium one (the reasoning counts in the output). Measured
# 17/11/2026 on 613 subtitles: 1.7 tokens a subtitle at medium (Sonnet 5.5), 8.4 at high (Sonnet 5.5), 11 at high (Opus 5.5).
EFFORT_OUTPUT = {"low": 1, "medium": 1, "high": 3, "xhigh": 5, "max": 8}
NAMES_PER_REQUEST = 70                         # review.PACKET_SIZE (review.py is not imported here: steps imports it)


def review(cfg: dict, mode: str, line_count: int, name_count: int) -> dict:
    """What the AI review of a video would take with each translator that can review: `name_count` names never
    submitted (both modes) and, for the full review, `line_count` subtitles read; tokens, cost (Claude API), tokens
    of the subscription (Claude Code) or duration."""
    r = settings(cfg)
    lines = line_count if mode == "full" else 0
    e = {"mode": mode, "count": lines, "names": name_count, "translators": []}
    for m in cfg.get("translators", []):
        kind = m.get("type", "openai")
        if kind not in ("claude", "claude_code", "openai"):
            continue
        requests = math.ceil(name_count / NAMES_PER_REQUEST) + (math.ceil(lines / corrections.block_lines(m)) if lines else 0)
        context = max(0, math.ceil(lines / corrections.block_lines(m)) - 1) * corrections.CONTEXT_LINES if lines else 0
        t = {"id": m.get("id"), "name": config.engine_name(m), "type": kind, "model": m.get("model", ""), "requests": requests,
             "input_tokens": round(requests * float(r["review_request_tokens"]) + (lines + context) * float(r["review_input_tokens_per_line"])
                                   + name_count * float(r["review_input_tokens_per_name"])),
             "output_tokens": round(lines * float(r["review_output_tokens_per_line"]) + name_count * float(r["review_output_tokens_per_name"]))}
        if kind in ("claude", "claude_code"):      # the effort chosen for its reviews (see ClaudeTranslator.review_effort)
            t["output_tokens"] = round(t["output_tokens"] * EFFORT_OUTPUT.get(m.get("review_effort") or "medium", 1))
        if kind == "claude":
            t["cost_usd"] = cost_usd(cfg, t["model"], t["input_tokens"], t["output_tokens"])
        elif kind == "claude_code":
            t["input_tokens"] += round(requests * float(r["claude_code_request_tokens"]))
            t.update(cost_usd=0.0, subscription=True, tokens=t["input_tokens"] + t["output_tokens"])
        else:
            t["duration_s"] = round(lines * float(r["review_local_seconds_per_line"]) + name_count * float(r["review_local_seconds_per_name"]))
            t["cost_usd"] = 0.0 if is_local(m) else None
        e["translators"].append(t)
    return e


def review_text(e: dict, engine_id: str | None) -> str:
    """Readable summary of the review estimate for the log: what is read, and what it takes with the model chosen."""
    what = i18n.t("est.review_full", n=e["count"], names=e["names"]) if e["mode"] == "full" else i18n.t("est.review_names", n=e["names"])
    t = next((x for x in e["translators"] if x["id"] == engine_id), None)
    if t is None:
        return what
    if t["type"] == "claude":
        detail = f"≈ {t['cost_usd']:.2f} $" if t.get("cost_usd") is not None else i18n.t("est.unknown_price")
    elif t.get("subscription"):
        detail = i18n.t("est.subscription", n=t["tokens"])
    else:
        detail = f"≈ {t['duration_s'] // 60} min {t['duration_s'] % 60:02d} s" + (
            " (" + i18n.t("est.free") + ")" if t.get("cost_usd") == 0 else ", " + i18n.t("est.unknown_price"))
    return what + "\n  " + i18n.t("est.line", name=f"{t['name']} ({t['model']})" if t.get("model") else t["name"], detail=detail)


def is_local(m: dict) -> bool:
    """Translator that runs on this machine (no cost): loopback URL."""
    url = (m.get("url") or "").lower()
    return m.get("type") == "openai" and any(h in url for h in ("127.0.0.1", "localhost", "[::1]"))


def pause_by_default(m: dict) -> bool:
    """Pause before translation to see the estimate: yes except for a local AI (free), Claude Code (on the
    subscription of the user: nothing billed) or no translation."""
    t = m.get("type", "openai")
    return not (t in ("none", "claude_code") or is_local(m))


def text(e: dict, cfg: dict | None = None) -> str:
    """Readable summary for the log."""
    if not e["languages"]:
        return i18n.t("est.nothing")
    parts = [i18n.t("est.summary", n=e["count"], langs=len(e["languages"]), requests=e["requests"], tokens_in=e["input_tokens"], tokens_out=e["output_tokens"])]
    for t in e["translators"]:
        if t["type"] == "claude":
            parts.append("  " + i18n.t("est.line", name=f"{t['name']} ({t['model']})", detail=f"≈ {t['cost_usd']:.2f} $" if t.get("cost_usd") is not None else i18n.t("est.unknown_price")))
        elif t.get("subscription"):
            parts.append("  " + i18n.t("est.line", name=t["name"], detail=i18n.t("est.subscription", n=t["tokens"])))
        elif t["type"] == "openai":
            parts.append("  " + i18n.t("est.line", name=t["name"], detail=f"≈ {t['duration_s'] // 60} min {t['duration_s'] % 60:02d} s") + (" (" + i18n.t("est.free") + ")" if t.get("cost_usd") == 0 else ""))
    return "\n".join(parts)
