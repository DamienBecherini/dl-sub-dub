"""Translation of the subtitles (into French or any other target language) in numbered chunks, keeping the timing.

Engines ("translators" entries of the configuration, `type` field):
  openai   OpenAI-compatible API: llama-server of the PC (started by the supervision if needed), Ollama, OpenAI, Mistral…
  claude   Anthropic API through the official SDK (key in the environment, never displayed)
  claude_code  Claude Code installed on this machine, signed in with a Claude subscription (no API key, nothing billed)
  none     no translation
Each subtitle is a numbered line; the answer must give back the same numbers. An incomplete chunk is sent again
once, then split into smaller chunks; as a last resort the line stays in its language.
The instructions sent to the model are written in French.
"""
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

from . import config, i18n, language

CLAUDE_CONCURRENCY = 5
MAX_CONCURRENCY = 20
MAX_CHUNKS_AT_ONCE = 8

SYSTEM_PROMPT = """Tu es un traducteur professionnel de sous-titres vidéo. Tu traduis en {langue}.
Règles :
- Une ligne numérotée en entrée donne exactement une ligne numérotée en sortie, avec le même numéro, dans le même ordre. Ne fusionne jamais deux lignes, n'en découpe aucune, n'en saute aucune.
- Les lignes sont des fragments consécutifs d'un même discours parlé : sers-toi des lignes voisines (et du contexte déjà traduit) pour le sens et les accords, mais chaque ligne doit rester compréhensible à sa place.
- Traduction naturelle et concise, registre de l'oral : un sous-titre se lit en quelques secondes. Reste proche de la longueur de l'original, jamais plus de 20 % plus long.
- Garde les noms propres, les chiffres, les noms de produits et les termes techniques d'usage courant en {langue}.
- Pas de commentaire, pas d'explication, pas de guillemets ajoutés : uniquement les lignes numérotées « N. texte »."""

_LINE = re.compile(r"^\s*(\d+)\s*[.):\-–]\s*(.*)$")


def instructions(target: str = "fr", terms: list[str] | None = None) -> str:
    """System instructions for a target language (ISO code), with the validated names for this video."""
    text = SYSTEM_PROMPT.format(langue=language.name(target))
    if terms:
        text += "\n\nNoms à respecter (« source → forme à employer » ; sans flèche, la forme usuelle dans la langue cible) :\n" + "\n".join("- " + t for t in terms)
    return text


class TranslationError(Exception):
    pass


def format_chunk(texts: list[str], context: list[str], translated: bool = True) -> str:
    """The lines to translate, numbered from 1, after the two lines before them: translated already, or in the original
    language when the chunk before is translated at the same time (chunks sent together)."""
    parts = []
    if context:
        title = ("Contexte déjà traduit (ne pas le reprendre dans la réponse) :" if translated
                 else "Contexte, les lignes précédentes en langue d'origine (ne pas les traduire ni les reprendre) :")
        parts.append(title + "\n" + "\n".join(context))
    parts.append("Lignes à traduire :\n" + "\n".join(f"{i + 1}. {t}" for i, t in enumerate(texts)))
    return "\n\n".join(parts)


def parse_answer(answer: str, expected: int) -> dict[int, str]:
    """Numbers → text; a line without a number continues the previous one (line break by the model)."""
    result: dict[int, str] = {}
    current = None
    for line in answer.splitlines():
        m = _LINE.match(line)
        if m and 1 <= int(m.group(1)) <= expected and (current is None or int(m.group(1)) > current):
            current = int(m.group(1))
            result[current] = m.group(2).strip()
        elif current is not None and line.strip():
            result[current] = (result[current] + " " + line.strip()).strip()
    return {k: v for k, v in result.items() if v}


def parse_json(answer: str) -> dict:
    """The JSON object of an answer, even inside a ```json block or with text around it (local models); TranslationError
    if there is none."""
    text = answer.strip()
    for candidate in (text, *re.findall(r"```(?:json)?\s*(.*?)```", text, flags=re.S), text[text.find("{"):text.rfind("}") + 1]):
        try:
            d = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(d, dict):
            return d
    raise TranslationError(i18n.t("err.answer_not_json", answer=text[:120]))


def _json(url, body=None, key="", timeout=10):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if data is not None else "GET",
                                 headers={"Content-Type": "application/json"})
    if key:
        req.add_header("Authorization", f"Bearer {key}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


class OpenAITranslator:
    """OpenAI-compatible API. With `supervision`, starts the local AI of the PC if it is stopped (local AI module),
    and stops it at the end if `stop_after` and if we started it."""

    def __init__(self, m: dict, log=print):
        self.url = m["url"].rstrip("/")
        self.model = m.get("model", "")
        self.supervision = (m.get("supervision") or "").rstrip("/")
        self.stop_after = bool(m.get("stop_after", False))
        self.key_reference = m.get("key", "")
        self.key = ""
        self.log = log
        self.started_here = False
        self.json_format = True                # `response_format` sent with a schema, until the server refuses it

    def _responds(self) -> bool:
        try:
            _json(self.url + "/v1/models", key=self.key, timeout=5)
            return True
        except urllib.error.HTTPError as e:
            if e.code in (401, 403) and not self.key and self.key_reference:
                self.key = config.resolve_key(self.key_reference)
                return self._responds()
            return False
        except (urllib.error.URLError, OSError, ValueError):
            return False

    def prepare(self, wait_s: int = 300):
        if self._responds():
            return
        if not self.supervision:
            raise TranslationError(i18n.t("err.llm_no_response", url=self.url))
        try:
            ai = _json(self.supervision + "/api/ai")
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise TranslationError(i18n.t("err.llm_and_supervision_down", url=self.url, supervision=self.supervision, error=e)) from e
        if ai.get("state") in ("stopped", "error", "safety_stop"):
            self.log(i18n.t("log.local_ai_starting"))
            _json(self.supervision + "/api/ai/start", {}, timeout=30)
            self.started_here = True
        start = time.time()
        while time.time() - start < wait_s:
            time.sleep(3)
            if self._responds():
                self.log(i18n.t("log.local_ai_ready", s=round(time.time() - start)))
                return
        raise TranslationError(i18n.t("err.local_ai_timeout"))

    def complete(self, system: str, request: str, schema: dict | None = None, effort: str | None = None) -> str:
        """`schema`: a JSON answer of this shape is asked for (`response_format`, and the shape written in the
        instructions for the servers and models that do not follow it); a server that refuses `response_format` is asked
        again without it. `effort` is Claude's: ignored here."""
        asked = system
        if schema:
            asked += ("\n\nRéponds uniquement par un objet JSON de cette forme (schéma JSON), sans texte autour :\n"
                      + json.dumps(schema, ensure_ascii=False))
        body = {"model": self.model, "temperature": 0.2, "max_tokens": 8192 if schema else 4096, "stream": False,
                "messages": [{"role": "system", "content": asked}, {"role": "user", "content": request}]}
        if schema and self.json_format:
            body["response_format"] = {"type": "json_schema", "json_schema": {"name": "answer", "schema": schema}}
        try:
            r = _json(self.url + "/v1/chat/completions", body, key=self.key, timeout=900)
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:300]
            if e.code in (400, 422) and "response_format" in body:      # this server does not know it: the instructions will do
                self.json_format = False
                return self.complete(system, request, schema, effort)
            raise TranslationError(f"{self.url} : {e.code} {detail}") from e
        except (urllib.error.URLError, OSError) as e:
            raise TranslationError(i18n.t("err.url_unreachable", url=self.url, error=e)) from e
        text = r["choices"][0]["message"]["content"] or ""
        return re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()

    def finish(self):
        if self.started_here and self.stop_after and self.supervision:
            try:
                _json(self.supervision + "/api/ai/stop", {}, timeout=60)
                self.log(i18n.t("log.local_ai_stopped"))
            except (urllib.error.URLError, OSError, ValueError) as e:
                self.log(i18n.t("log.local_ai_stop_failed", error=e))


class ClaudeTranslator:
    """Anthropic API through the official SDK (pip install anthropic, in the .venv of the repository)."""

    def __init__(self, m: dict, log=print):
        try:
            import anthropic
        except ImportError as e:
            raise TranslationError(i18n.t("err.anthropic_sdk_missing")) from e
        key = config.resolve_key(m.get("key", "")) or os.environ.get("ANTHROPIC_API_KEY", "")
        if not key:
            raise TranslationError(i18n.t("err.anthropic_key_missing"))
        self.anthropic = anthropic
        # Several videos at once can reach the rate limit: the SDK waits and retries (`retry-after`).
        self.client = anthropic.Anthropic(api_key=key, max_retries=8)
        self.model = m.get("model", "claude-sonnet-5-5")
        self.effort = m.get("effort") or "low"
        self.review_effort = m.get("review_effort") or "medium"     # for the AI review (see corrections.py): it reasons more than a translation
        self.log = log
        self.input_tokens = 0
        self.output_tokens = 0

    def prepare(self):
        pass

    def complete(self, system: str, request: str, schema: dict | None = None, effort: str | None = None) -> str:
        """`schema`: a JSON answer of this shape (the AI review). Such an answer can be long (a whole episode read, at
        a high effort the reasoning counts in the output): it is streamed, so that nothing times out, with room for
        64 000 tokens; an answer cut all the same is an error rather than half a JSON."""
        a = self.anthropic
        output = {"effort": effort or self.effort, **({"format": {"type": "json_schema", "schema": schema}} if schema else {})}
        # Server-side fallback when the classifiers refuse (routing by category, without a list of models).
        asked = dict(model=self.model, system=system, messages=[{"role": "user", "content": request}], output_config=output,
                     betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        try:
            if schema:
                with self.client.beta.messages.stream(max_tokens=64000, **asked) as stream:
                    resp = stream.get_final_message()
            else:
                resp = self.client.beta.messages.create(max_tokens=16000, **asked)
        except a.RateLimitError as e:
            raise TranslationError(i18n.t("err.anthropic_rate_limit", s=e.response.headers.get("retry-after", "?"))) from e
        except a.APIStatusError as e:
            raise TranslationError(i18n.t("err.anthropic_status", code=e.status_code, detail=e.message)) from e
        except a.APIConnectionError as e:
            raise TranslationError(i18n.t("err.anthropic_unreachable", error=e)) from e
        if resp.stop_reason == "refusal":
            detail = getattr(resp, "stop_details", None)
            raise TranslationError(i18n.t("err.anthropic_refused", category=getattr(detail, "category", "?")))
        u = getattr(resp, "usage", None)
        if u:
            self.input_tokens += getattr(u, "input_tokens", 0) or 0
            self.output_tokens += getattr(u, "output_tokens", 0) or 0
        if schema and resp.stop_reason == "max_tokens":
            raise TranslationError(i18n.t("err.anthropic_cut"))
        return "".join(b.text for b in resp.content if b.type == "text").strip()

    def finish(self):
        if self.input_tokens or self.output_tokens:
            from . import estimation
            cost = estimation.cost_usd(config.load(), self.model, self.input_tokens, self.output_tokens)
            self.log(i18n.t("log.anthropic_usage", model=self.model, tokens_in=self.input_tokens, tokens_out=self.output_tokens)
                     + (f" ≈ {cost:.2f} $" if cost is not None else ""))


_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def claude_code_program(m: dict | None = None) -> str:
    """Path of Claude Code on this machine, "" if it is not installed: the `program` field of the translator, otherwise
    `claude` in the PATH."""
    return shutil.which(str((m or {}).get("program") or "claude")) or ""


def _claude_code_env() -> dict:
    """The environment given to Claude Code: without what would make it bill an API (a key, another provider) and
    without the variables of a Claude Code session the service was started from. Its sign-in is kept
    (CLAUDE_CONFIG_DIR)."""
    return {k: v for k, v in os.environ.items()
            if not (k.startswith("ANTHROPIC_") or k.startswith("CLAUDE_CODE_") or k in ("CLAUDECODE", "AWS_BEARER_TOKEN_BEDROCK"))}


def claude_code_status(m: dict | None = None) -> dict:
    """{installed, signed_in, subscription}: whether Claude Code is on this machine and signed in with a Claude
    subscription ("pro", "max"…; "" when it is signed in otherwise: a Console account, a key, another provider, which
    would be billed)."""
    program = claude_code_program(m)
    if not program:
        return {"installed": False, "signed_in": False, "subscription": ""}
    try:
        r = subprocess.run([program, "auth", "status", "--json"], capture_output=True, timeout=30, env=_claude_code_env(),
                           stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW)
        d = json.loads(r.stdout.decode("utf-8", "replace") or "{}")
    except (OSError, subprocess.SubprocessError, ValueError):
        d = {}
    if not isinstance(d, dict):
        d = {}
    with_plan = d.get("loggedIn") is True and d.get("authMethod") == "claude.ai"
    return {"installed": True, "signed_in": d.get("loggedIn") is True,
            "subscription": str(d.get("subscriptionType") or "claude.ai") if with_plan else ""}


class ClaudeCodeTranslator:
    """Claude Code of this machine, run once per request without a terminal (`claude -p`), on the Claude subscription
    of the user: no API key, nothing billed, the usage counts in the limits of the subscription. It runs bare: no
    tools, none of the user's hooks, instructions, skills or MCP servers (--safe-mode), no conversation kept. Refused
    when Claude Code is not signed in with a subscription: it would then spend on an API without a question."""

    def __init__(self, m: dict, log=print):
        self.program = claude_code_program(m)
        if not self.program:
            raise TranslationError(i18n.t("err.claude_code_missing"))
        self.settings = m
        self.model = m.get("model") or "sonnet"         # an alias (sonnet, opus) or a full name
        self.effort = m.get("effort") or "low"
        self.review_effort = m.get("review_effort") or "medium"
        self.log = log
        self.checked = False
        self.input_tokens = 0
        self.output_tokens = 0

    def prepare(self):
        if self.checked:
            return
        s = claude_code_status(self.settings)
        if not s["signed_in"]:
            raise TranslationError(i18n.t("err.claude_code_signed_out"))
        if not s["subscription"]:
            raise TranslationError(i18n.t("err.claude_code_no_subscription"))
        self.checked = True

    def complete(self, system: str, request: str, schema: dict | None = None, effort: str | None = None) -> str:
        """The instructions go through a file and the request through the standard input (no limit of length, no
        quoting); `schema`: a JSON answer of this shape, checked by Claude Code itself."""
        self.prepare()
        with tempfile.TemporaryDirectory(prefix="dl-sub-dub-claude-") as folder:
            instructions = Path(folder) / "instructions.txt"
            instructions.write_text(system, encoding="utf-8")
            command = [self.program, "-p", "--safe-mode", "--tools", "", "--strict-mcp-config", "--no-session-persistence",
                       "--disable-slash-commands", "--model", self.model, "--effort", effort or self.effort,
                       "--output-format", "json", "--system-prompt-file", str(instructions)]
            if schema:
                command += ["--json-schema", json.dumps(schema, ensure_ascii=True)]
            try:
                r = subprocess.run(command, input=request.encode("utf-8"), capture_output=True, timeout=1800, cwd=folder,
                                   env=_claude_code_env(), creationflags=_NO_WINDOW)
            except subprocess.TimeoutExpired as e:
                raise TranslationError(i18n.t("err.claude_code_failed", detail="timeout")) from e
            except OSError as e:
                raise TranslationError(i18n.t("err.claude_code_failed", detail=e)) from e
        out = r.stdout.decode("utf-8", "replace").strip()
        try:
            d = json.loads(out)
        except ValueError:
            d = None
        if not isinstance(d, dict):
            raise TranslationError(i18n.t("err.claude_code_failed", detail=(r.stderr.decode("utf-8", "replace").strip() or out)[:300]))
        if d.get("is_error") or d.get("subtype") not in (None, "success"):
            raise TranslationError(i18n.t("err.claude_code_failed", detail=str(d.get("result") or d.get("subtype") or "?")[:300]))
        u = d.get("usage") or {}
        self.input_tokens += sum(int(u.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
        self.output_tokens += int(u.get("output_tokens") or 0)
        if schema and d.get("structured_output") is not None:
            return json.dumps(d["structured_output"], ensure_ascii=False)
        return str(d.get("result") or "").strip()

    def finish(self):
        if self.input_tokens or self.output_tokens:
            self.log(i18n.t("log.claude_code_usage", model=self.model, tokens_in=self.input_tokens, tokens_out=self.output_tokens))


def create(m: dict, log=print):
    t = m.get("type", "openai")
    if t == "openai":
        tr = OpenAITranslator(m, log)
    elif t == "claude":
        tr = ClaudeTranslator(m, log)
    elif t == "claude_code":
        tr = ClaudeCodeTranslator(m, log)
    else:
        raise TranslationError(i18n.t("err.unknown_translator_type", type=t))
    tr.at_once = chunks_at_once(m)                  # chunks of a video sent together (translate_texts)
    return tr


def concurrency(m: dict) -> int:
    """Videos of a project translated at once: `parallel` field of the translator, else 5 for Claude (remote API)
    and 1 for the others (a local AI handles only one; Claude Code spares the limits of the subscription)."""
    try:
        n = int(m.get("parallel") or (CLAUDE_CONCURRENCY if m.get("type") == "claude" else 1))
    except (TypeError, ValueError):
        n = 1
    return max(1, min(n, MAX_CONCURRENCY))


def test(m: dict) -> str:
    """Short exchange to check that a translator answers; returns its answer."""
    tr = create(m, lambda _msg: None)
    try:
        tr.prepare()
        return tr.complete("Réponds en un mot.", "Dis « prêt ».")[:80]
    finally:
        tr.finish()


def chunks_at_once(m: dict) -> int:
    """Chunks of one video sent at once (`chunks_at_once` field of the translator, 1 by default): worth it with a
    server that answers several requests together (llama.cpp with --parallel 2 or more, a remote API)."""
    try:
        return max(1, min(int(m.get("chunks_at_once") or 1), MAX_CHUNKS_AT_ONCE))
    except (TypeError, ValueError):
        return 1


def _in_order(translator, texts: list[str], first: int, result: list, context: list[str], chunk_size: int, system: str,
              log, cancelled, done=lambda position: None) -> list[str]:
    """Translates texts[first:] into result, one chunk after the other, each with the two lines translated before it;
    an incomplete answer: the chunk again, halved. Returns the context after the last chunk."""
    position, chunk = first, chunk_size
    end = first + len(texts)
    while position < end:
        if cancelled():
            raise TranslationError(i18n.t("err.cancelled"))
        piece = texts[position - first:position - first + chunk]
        got = _translate_chunk(translator, piece, context, system)
        missing = [i for i in range(len(piece)) if (i + 1) not in got]
        if missing and chunk > 1:
            chunk = max(1, chunk // 2)
            log("  " + i18n.t("log.chunk_incomplete", n=len(missing), size=chunk))
            continue
        for i, t in enumerate(piece):
            result[position + i] = got.get(i + 1, t)
            if (i + 1) not in got:
                log("  " + i18n.t("log.line_untranslated", line=position + i + 1, text=t[:60]))
        context = [f"{position + i + 1}. {result[position + i]}" for i in range(max(0, len(piece) - 2), len(piece))]
        position += len(piece)
        done(position)
        chunk = min(chunk_size, chunk * 2) if chunk < chunk_size else chunk
    return context


def translate_texts(translator, texts: list[str], chunk_size: int = 25, log=print, cancelled=lambda: False,
                    target: str = "fr", terms: list[str] | None = None, progress=None) -> list[str]:
    """Translates in order into the `target` language; a line the model did not give back stays as it is (reported).
    With `at_once` > 1 on the translator, that many chunks are sent together: the first of them gets the two lines
    translated before it as context, the others the two lines of the original before them (not translated yet)."""
    system = instructions(target, terms)
    translator.prepare()
    result: list = [None] * len(texts)

    def done(position):
        log("  " + i18n.t("log.translated_progress", done=position, total=len(texts)))
        if progress:
            progress(position, len(texts))

    at_once = max(1, int(getattr(translator, "at_once", 1) or 1))
    if at_once == 1:
        _in_order(translator, texts, 0, result, [], chunk_size, system, log, cancelled, done)
        return result
    starts = list(range(0, len(texts), chunk_size))
    context_of = lambda p: [f"{j + 1}. {result[j] if result[j] is not None else texts[j]}"   # noqa: E731
                            for j in range(max(0, p - 2), p)]
    with ThreadPoolExecutor(max_workers=at_once, thread_name_prefix="translate") as pool:
        for w in range(0, len(starts), at_once):
            if cancelled():
                raise TranslationError(i18n.t("err.cancelled"))
            wave = starts[w:w + at_once]
            jobs = [(p, texts[p:p + chunk_size], context_of(p)) for p in wave]
            log("  " + i18n.t("log.translating_wave", first=wave[0] + 1, last=min(len(texts), wave[-1] + chunk_size),
                              n=len(wave)))           # the answers come together: said when they leave
            answers = [pool.submit(_translate_chunk, translator, piece, ctx, system, p == 0 or result[p - 1] is not None)
                       for p, piece, ctx in jobs]
            for (p, piece, ctx), answer in zip(jobs, answers):
                got = answer.result()
                if all((i + 1) in got for i in range(len(piece))):
                    result[p:p + len(piece)] = [got[i + 1] for i in range(len(piece))]
                else:                          # incomplete: this chunk again, alone and halved, as in order
                    log("  " + i18n.t("log.chunk_incomplete", n=sum((i + 1) not in got for i in range(len(piece))), size=max(1, chunk_size // 2)))
                    _in_order(translator, piece, p, result, ctx, max(1, chunk_size // 2), system, log, cancelled)
            done(min(len(texts), wave[-1] + chunk_size))
    return result


def _translate_chunk(translator, piece: list[str], context: list[str], system: str, translated: bool = True) -> dict[int, str]:
    answer = translator.complete(system, format_chunk(piece, context, translated))
    got = parse_answer(answer, len(piece))
    if len(got) < len(piece):
        reminder = format_chunk(piece, context, translated) + (f"\n\nRappel : réponds avec exactement {len(piece)} lignes numérotées de 1 à "
                                                   f"{len(piece)}, une par ligne d'entrée.")
        got = {**parse_answer(translator.complete(system, reminder), len(piece)), **got}
    return got
