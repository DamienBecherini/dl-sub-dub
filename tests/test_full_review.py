"""The AI review: which model reviews, a JSON answer from any model, the full review of a video (its lines fixed,
decided, applied before the translation), its estimate, and the pauses of a task (the cost of the review, its
proposals, the cost of the translation)."""
import io
import json
import urllib.error

import pytest

from subtitles import config, corrections, estimation, pipeline, review, segmentation, steps, translation
from subtitles.states import TaskState
from subtitles.web.manager import Manager
from subtitles.web.tasks import TaskQueue
from helpers import Run

CLAUDE = {"id": "claude", "type": "claude", "name": "Claude", "model": "claude-sonnet-5-5"}
LOCAL = {"id": "local", "type": "openai", "name": "Local", "url": "http://127.0.0.1:8080", "model": "qwen"}
REMOTE = {"id": "remote", "type": "openai", "name": "Remote", "url": "https://api.example.com", "model": "big"}
NONE = {"id": "none", "type": "none", "name": "None"}
CODE = {"id": "code", "type": "claude_code", "name": "Claude Code", "model": "sonnet"}


# --- which model reviews ---------------------------------------------------------------------------------------------

def test_the_model_chosen_for_the_review_reviews():
    cfg = {"translators": [CLAUDE, LOCAL, REMOTE], "active_translator": "claude"}
    assert review.engine(cfg, "claude", "local")["id"] == "local"          # any kind of translator
    assert review.engine(cfg, "local", "remote")["id"] == "remote"


def test_without_a_choice_a_claude_translator_otherwise_the_project_translator():
    assert review.engine({"translators": [LOCAL, CLAUDE], "active_translator": "local"}, "local")["id"] == "claude"     # as before
    assert review.engine({"translators": [LOCAL, REMOTE], "active_translator": "local"}, "remote")["id"] == "remote"
    assert review.engine({"translators": [LOCAL], "active_translator": "local"})["id"] == "local"                      # the active one
    assert review.engine({"translators": [NONE], "active_translator": "none"}) is None
    assert review.engine({"translators": [LOCAL], "active_translator": "local"}, "local", "gone")["id"] == "local"     # a model removed since


def test_without_a_choice_claude_code_reviews_before_the_paid_api():
    assert review.engine({"translators": [LOCAL, CLAUDE, CODE], "active_translator": "local"}, "local")["id"] == "code"
    assert review.engine({"translators": [LOCAL, CLAUDE, CODE], "active_translator": "claude"}, "claude")["id"] == "claude"    # the project's own
    assert review.engine({"translators": [LOCAL, CODE], "active_translator": "local"}, "local", "local")["id"] == "local"     # chosen


def test_the_review_asked_for_reads_the_old_yes():
    assert [review.mode(v) for v in (True, False, None, "", "names", "full", "other")] == ["names", "", "", "", "names", "full", ""]


# --- a JSON answer from an OpenAI-compatible model -------------------------------------------------------------------

def answer(text):
    return {"choices": [{"message": {"content": text}}]}


def test_an_openai_model_is_asked_for_json_of_the_shape(monkeypatch):
    sent = []
    monkeypatch.setattr(translation, "_json", lambda url, body=None, key="", timeout=10: sent.append(body) or answer('{"corrections": []}'))
    tr = translation.OpenAITranslator(LOCAL)
    assert tr.complete("Relis.", "1. a", schema=corrections.SCHEMA, effort="medium") == '{"corrections": []}'
    assert sent[0]["response_format"]["json_schema"]["schema"] == corrections.SCHEMA
    assert "JSON" in sent[0]["messages"][0]["content"] and sent[0]["messages"][0]["content"].startswith("Relis.")
    tr.complete("Traduis.", "1. a")
    assert "response_format" not in sent[1] and sent[1]["messages"][0]["content"] == "Traduis."


def test_a_server_that_refuses_the_format_is_asked_again_without_it(monkeypatch):
    sent = []

    def post(url, body=None, key="", timeout=10):
        sent.append(body)
        if "response_format" in body:
            raise urllib.error.HTTPError(url, 400, "Bad Request", {}, io.BytesIO(b"response_format is not supported"))
        return answer('Voici :\n```json\n{"corrections": []}\n```')
    monkeypatch.setattr(translation, "_json", post)
    tr = translation.OpenAITranslator(REMOTE)
    assert translation.parse_json(tr.complete("Relis.", "1. a", schema=corrections.SCHEMA)) == {"corrections": []}
    assert len(sent) == 2 and sent[1]["messages"][0]["content"].count("JSON") == sent[0]["messages"][0]["content"].count("JSON")
    tr.complete("Relis.", "1. b", schema=corrections.SCHEMA)
    assert "response_format" not in sent[2]                # remembered for the next requests


def test_json_is_read_inside_a_block_or_with_text_around():
    assert translation.parse_json('{"a": 1}') == {"a": 1}
    assert translation.parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert translation.parse_json('Bien sûr ! {"a": {"b": 2}} Voilà.') == {"a": {"b": 2}}
    with pytest.raises(translation.TranslationError, match="illisible"):
        translation.parse_json("Je ne sais pas.")


# --- the full review of a video ---------------------------------------------------------------------------------------

class Reviewer:
    """A model that answers what it is told to, for each request in turn (a list: the corrections; an exception: raised)."""

    def __init__(self, answers):
        self.answers, self.requests, self.systems, self.names, self.prepared, self.finished = list(answers), [], [], [], 0, 0

    def prepare(self):
        self.prepared += 1

    def complete(self, system, request, schema=None, effort=None):
        if "entries" in schema["properties"]:              # the review of the names: nothing to say
            self.names.append(request)
            return json.dumps({"entries": []})
        self.requests.append(request)
        self.systems.append(system)
        a = self.answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a if isinstance(a, str) else json.dumps({"corrections": a})

    def finish(self):
        self.finished += 1


def words_of(sentences):
    """Words with their times, a word every half second, a silence of one second between two sentences."""
    out, at = [], 0.0
    for s in sentences:
        for w in s.split():
            out.append({"word": w, "start": round(at, 2), "end": round(at + 0.4, 2)})
            at += 0.5
        at += 1.0
    return out


SENTENCES = ["We met Peter in the lavatory.", "He works on the genome.", "Thank you for watching!", "The cells emit light."]


def cues_of(sentences=SENTENCES):
    return segmentation.build(segmentation.from_json(words_of(sentences)))


def fix(line, corrected, confidence="high"):
    return {"line": line, "corrected": corrected, "reason": "d'après le contexte", "confidence": confidence}


def test_the_review_keeps_the_lines_to_fix_and_only_them(tmp_path):
    cues = cues_of()
    model = Reviewer([[fix(1, "We met Peter in the laboratory."), fix(2, "He works on the Genome!"),      # capitals, punctuation: nothing to fix
                       fix(3, ""), fix(4, "The cells emit light. " * 3),                              # grown by other lines: left out
                       fix(9, "out of the video"), {"line": "x"}]])
    n = corrections.review(CLAUDE, tmp_path, "Episode 1", cues, ["Peter Gariaev"], log=lambda _m: None, create=lambda m, log: model)
    assert n == 2 and model.prepared == 1 and model.finished == 1
    assert "Titre : Episode 1" in model.requests[0] and "Noms déjà vérifiés : Peter Gariaev" in model.requests[0]
    assert "1. We met Peter in the lavatory." in model.requests[0] and "4. The cells emit light." in model.requests[0]
    assert "en français" in model.systems[0]               # the reasons in the language of the interface
    a, b = corrections.pending(tmp_path)
    assert (a["line"], a["original"], a["corrected"], a["status"]) == (1, "We met Peter in the lavatory.", "We met Peter in the laboratory.", "pending")
    assert (a["start"], a["end"]) == (0.0, 2.9) and b["corrected"] == ""        # the times of its words; a sentence never said
    assert corrections.reviewed(tmp_path)
    assert corrections.review(CLAUDE, tmp_path, "Episode 1", cues, [], log=lambda _m: None, create=lambda m, log: model) == 2
    assert len(model.requests) == 1                        # a video reviewed is not reviewed twice


def test_a_long_video_is_read_in_blocks_with_the_lines_before(tmp_path):
    cues = cues_of([f"Sentence number {i} goes here." for i in range(1, 51)])
    model = Reviewer([[fix(3, "Sentence number three goes here.")], [], [fix(50, "Sentence number fifty goes here.")]])
    done = []
    engine = {**LOCAL, "review_lines": 20}
    assert corrections.review(engine, tmp_path, "T", cues, log=lambda _m: None, create=lambda m, log: model, progress=lambda d, n: done.append((d, n))) == 2
    assert done == [(20, 50), (40, 50), (50, 50)]
    assert "Contexte" not in model.requests[0] and "21. Sentence number 21 goes here." in model.requests[1]
    assert "20. Sentence number 20 goes here." in model.requests[1].split("Lignes à relire")[0]      # given again, not to fix
    assert "« Sentence number 3 goes here. » → « Sentence number three goes here. »" in model.requests[1]      # what was fixed above
    assert "Corrections déjà proposées" not in model.requests[0]
    assert [c["line"] for c in corrections.pending(tmp_path)] == [3, 50]
    assert corrections.block_lines(CLAUDE) == 800 and corrections.block_lines(LOCAL) == 120


def test_a_failure_leaves_the_rest_to_review_next_time(tmp_path):
    cues = cues_of([f"Sentence number {i} goes here." for i in range(1, 41)])
    engine = {**LOCAL, "review_lines": 20}
    broken = Reviewer([[fix(1, "Sentence number one goes here.")], "pas du JSON", "toujours pas"])
    with pytest.raises(translation.TranslationError):
        corrections.review(engine, tmp_path, "T", cues, log=lambda _m: None, create=lambda m, log: broken)
    assert broken.finished == 1 and len(broken.requests) == 3            # an answer that cannot be read is asked once more
    assert not corrections.reviewed(tmp_path) and len(corrections.pending(tmp_path)) == 1
    again = Reviewer([[fix(30, "Sentence number thirty goes here.")]])
    assert corrections.review(engine, tmp_path, "T", cues, log=lambda _m: None, create=lambda m, log: again) == 2
    assert "21. Sentence" in again.requests[0] and "\n1. Sentence" not in again.requests[0]       # from where it stopped
    assert corrections.reviewed(tmp_path)


def test_a_decision_accepts_rejects_or_retouches(tmp_path):
    model = Reviewer([[fix(1, "We met Peter in the laboratory."), fix(3, ""), fix(4, "The cells emit bright light.")]])
    corrections.review(CLAUDE, tmp_path, "T", cues_of(), log=lambda _m: None, create=lambda m, log: model)
    a, b, c = corrections.pending(tmp_path)
    assert corrections.decide(tmp_path, [{"id": a["id"], "accept": True, "corrected": "We met Peter in the  lab."},
                                         {"id": b["id"], "accept": False}, {"id": "unknown", "accept": True}]) == (1, 1)
    assert [x["id"] for x in corrections.pending(tmp_path)] == [c["id"]]
    assert [(x["corrected"], x["status"]) for x in corrections.accepted(tmp_path)] == [("We met Peter in the lab.", "accepted")]
    assert corrections.decide(tmp_path, [{"id": c["id"], "accept": True, "corrected": c["original"]}]) == (0, 1)      # nothing changed: brings nothing


def test_a_correction_replaces_the_words_of_its_line():
    words = words_of(SENTENCES)
    same = {"start": 0.0, "end": 2.9, "corrected": "We met Peter in the laboratory."}
    out, n = corrections.apply(words, [same])
    assert n == 1 and [w["word"] for w in out[:6]] == ["We", "met", "Peter", "in", "the", "laboratory."]
    assert [(w["start"], w["end"]) for w in out[:6]] == [(w["start"], w["end"]) for w in words[:6]]       # as many words: their times
    shorter = {"start": 0.0, "end": 2.9, "corrected": "We met Peter there."}
    out, n = corrections.apply(words, [shorter])
    assert [w["word"] for w in out[:4]] == ["We", "met", "Peter", "there."] and (out[0]["start"], out[3]["end"]) == (0.0, 2.9)
    assert all(a["end"] <= b["start"] + 1e-6 for a, b in zip(out, out[1:])) and len(out) == len(words) - 2
    removed = {"start": 7.5, "end": 9.4, "corrected": ""}                # "Thank you for watching!"
    out, n = corrections.apply(words, [removed, same])
    assert n == 2 and "watching!" not in [w["word"] for w in out] and out[5]["word"] == "laboratory."
    assert corrections.apply(words, [{"start": 500.0, "end": 502.0, "corrected": "elsewhere"}]) == (words, 0)       # another transcription
    assert words[5]["word"] == "lavatory."                 # the transcription itself is never changed


def project_folder(tmp_path, sentences=SENTENCES):
    """A video file with its transcription kept: `pipeline.prepare` takes it back without any server."""
    video = tmp_path / "Episode 1.mp4"
    video.write_bytes(b"x")
    work = pipeline.work_dir(tmp_path, video)
    work.mkdir(parents=True)
    (work / "transcription.json").write_text(json.dumps({"words": words_of(sentences), "segments": [], "language": "en", "text": " ".join(sentences)}), encoding="utf-8")
    return video, work


def settings(tmp_path, translators, active="local"):
    return {"output_dir": str(tmp_path), "transcriptions": [{"id": "t", "url": "http://127.0.0.1:8000"}], "active_transcription": "t",
            "translators": translators, "active_translator": active, "target_languages": ["fr"], "cleanup": {}}


def test_the_subtitles_are_cut_with_the_corrections_accepted(tmp_path):
    video, work = project_folder(tmp_path)
    cfg = settings(tmp_path, [LOCAL])
    p = pipeline.prepare(pipeline.Request(str(video), cfg), log=lambda _m: None)
    assert [c.text for c in p.cues][0] == "We met Peter in the lavatory."
    model = Reviewer([[fix(1, "We met Peter in the laboratory."), fix(3, "")]])
    corrections.review(LOCAL, work, "T", p.cues, log=lambda _m: None, create=lambda m, log: model)
    assert [c.text for c in pipeline.prepare(pipeline.Request(str(video), cfg), log=lambda _m: None).cues] == [c.text for c in p.cues]     # nothing decided
    corrections.decide(work, [{"id": c["id"], "accept": True} for c in corrections.pending(work)])
    log = []
    p = pipeline.prepare(pipeline.Request(str(video), cfg), log=log.append)
    assert [c.text for c in p.cues] == ["We met Peter in the laboratory.", "He works on the genome.", "The cells emit light."]
    assert "laboratory" in p.source_srt.read_text(encoding="utf-8") and any("2 lignes corrigées" in m for m in log)
    assert "lavatory" in (work / "transcription.json").read_text(encoding="utf-8")


def test_the_steps_of_a_video_wait_for_its_lines_too(tmp_path):
    video, work = project_folder(tmp_path)
    assert not steps.observe(work, video, ["fr"], "en", set())["reviewed"]
    model = Reviewer([[fix(1, "We met Peter in the laboratory.")]])
    corrections.review(LOCAL, work, "T", cues_of(), log=lambda _m: None, create=lambda m, log: model)
    seen = steps.observe(work, video, ["fr"], "en", set())
    assert seen["reviewed"] and not seen["validated"]
    corrections.decide(work, [{"id": corrections.pending(work)[0]["id"], "accept": False}])
    assert steps.observe(work, video, ["fr"], "en", set())["validated"]


# --- what it will take -----------------------------------------------------------------------------------------------

def test_the_review_is_estimated_for_each_model():
    cfg = {"translators": [CLAUDE, LOCAL, REMOTE, NONE]}
    e = estimation.review(cfg, "full", 500, 30)
    claude, local, remote = e["translators"]
    assert (e["mode"], e["count"], e["names"]) == ("full", 500, 30) and [t["id"] for t in e["translators"]] == ["claude", "local", "remote"]
    assert claude["requests"] == 2 and local["requests"] == 6                  # the names, then 800 or 120 lines at a time
    assert claude["cost_usd"] == estimation.cost_usd(cfg, CLAUDE["model"], claude["input_tokens"], claude["output_tokens"]) > 0
    assert local["cost_usd"] == 0.0 and local["duration_s"] > 0 and remote["cost_usd"] is None
    high = estimation.review({"translators": [{**CLAUDE, "review_effort": "high"}]}, "full", 500, 30)["translators"][0]
    assert high["output_tokens"] == 3 * claude["output_tokens"] and high["cost_usd"] > claude["cost_usd"]      # it reasons more
    names = estimation.review(cfg, "names", 500, 30)
    assert names["count"] == 0 and names["translators"][0]["input_tokens"] < claude["input_tokens"] / 5
    assert "500 sous-titres" in estimation.review_text(e, "claude") and "$" in estimation.review_text(e, "claude")
    assert "gratuit" in estimation.review_text(e, "local") and "prix inconnu" in estimation.review_text(e, "remote")


def test_claude_code_is_estimated_in_tokens_of_the_subscription():
    cfg = {"translators": [CLAUDE, CODE, {**CODE, "id": "code-high", "review_effort": "high"}]}
    e = estimation.review(cfg, "full", 500, 30)
    claude, code, high = e["translators"]
    assert (code["cost_usd"], code["subscription"], code["requests"]) == (0.0, True, 2)
    assert code["tokens"] == code["input_tokens"] + code["output_tokens"] == claude["input_tokens"] + claude["output_tokens"] + 2 * 1000
    assert high["output_tokens"] == 3 * code["output_tokens"]                 # the effort counts as for the API
    assert "abonnement Claude Code" in estimation.review_text(e, "code") and "$" not in estimation.review_text(e, "code")
    t = estimation.estimate(cfg, 500, ["fr"], "en")
    code = t["translators"][1]
    assert (code["cost_usd"], code["subscription"]) == (0.0, True) and code["tokens"] == t["input_tokens"] + t["output_tokens"] + 20 * 1000
    assert "abonnement Claude Code" in estimation.text(t, cfg)


# --- Claude Code of the machine as a model -----------------------------------------------------------------------------

class Program:
    """Claude Code replaced: what it is asked (the commands, the request, the environment), what it answers."""

    def __init__(self, status=None, answers=()):
        self.status = {"loggedIn": True, "authMethod": "claude.ai", "subscriptionType": "pro"} if status is None else status
        self.answers, self.calls = list(answers), []

    def run(self, command, **options):
        self.calls.append((command, options))
        if command[1:3] == ["auth", "status"]:
            return translation.subprocess.CompletedProcess(command, 0, json.dumps(self.status).encode(), b"")
        self.instructions = open(command[command.index("--system-prompt-file") + 1], encoding="utf-8").read()
        out, code = self.answers.pop(0), 0
        if not isinstance(out, (dict, list)):
            return translation.subprocess.CompletedProcess(command, 1, b"", str(out).encode())
        return translation.subprocess.CompletedProcess(command, code, json.dumps(out).encode(), b"")


def claude_code(monkeypatch, program, found="/bin/claude"):
    monkeypatch.setattr(translation.shutil, "which", lambda name: found)
    monkeypatch.setattr(translation.subprocess, "run", program.run)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "would-be-billed")
    monkeypatch.setenv("CLAUDE_CODE_USE_BEDROCK", "1")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/home/someone/.claude")
    return program


def test_claude_code_answers_bare_on_the_subscription(monkeypatch):
    usage = {"input_tokens": 2, "cache_creation_input_tokens": 2000, "output_tokens": 100}
    program = claude_code(monkeypatch, Program(answers=[
        {"subtype": "success", "is_error": False, "result": "ignored", "structured_output": {"corrections": []}, "usage": usage},
        {"subtype": "success", "is_error": False, "result": " 1. Bonjour ", "usage": usage}]))
    said = []
    tr = translation.create({**CODE, "review_effort": "high"}, said.append)
    assert translation.parse_json(tr.complete("Relis.", "1. a", schema=corrections.SCHEMA, effort=tr.review_effort)) == {"corrections": []}
    assert tr.complete("Traduis.", "1. Hello") == "1. Bonjour"
    (status, _), (first, options), (second, _) = program.calls                   # the sign-in is checked once
    assert status[1:] == ["auth", "status", "--json"]
    assert first[1] == "-p" and "--safe-mode" in first and first[first.index("--tools") + 1] == "" and "--no-session-persistence" in first
    assert first[first.index("--model") + 1] == "sonnet" and first[first.index("--effort") + 1] == "high"
    assert json.loads(first[first.index("--json-schema") + 1]) == corrections.SCHEMA and options["input"] == b"1. a"
    assert "--json-schema" not in second and second[second.index("--effort") + 1] == "low" and program.instructions == "Traduis."
    env = options["env"]                                    # nothing that would bill an API; the sign-in kept
    assert "ANTHROPIC_API_KEY" not in env and "CLAUDE_CODE_USE_BEDROCK" not in env and env["CLAUDE_CONFIG_DIR"] == "/home/someone/.claude"
    tr.finish()
    assert "4004" in said[-1] and "200" in said[-1] and "abonnement" in said[-1]


def test_claude_code_is_refused_without_a_subscription(monkeypatch):
    for status, word in (({"loggedIn": False}, "pas connecté"), ({"loggedIn": True, "authMethod": "console"}, "facturé"),
                         ({"loggedIn": True, "authMethod": "api_key"}, "facturé"), ("not json", "pas connecté")):
        program = claude_code(monkeypatch, Program(status=status))
        with pytest.raises(translation.TranslationError, match=word):
            translation.create(CODE).complete("Relis.", "1. a")
        assert len(program.calls) == 1                     # nothing asked to a model
    claude_code(monkeypatch, Program(), found=None)
    with pytest.raises(translation.TranslationError, match="pas installé"):
        translation.create(CODE)
    assert translation.claude_code_status() == {"installed": False, "signed_in": False, "subscription": ""}


def test_what_claude_code_refuses_is_said(monkeypatch):
    claude_code(monkeypatch, Program(answers=[
        {"subtype": "success", "is_error": True, "result": "Claude usage limit reached. Your limit will reset at 5pm."}, "boom on stderr"]))
    tr = translation.create(CODE)
    with pytest.raises(translation.TranslationError, match="usage limit reached"):
        tr.complete("Relis.", "1. a")
    with pytest.raises(translation.TranslationError, match="boom on stderr"):
        tr.complete("Relis.", "1. a")
    assert translation.claude_code_status() == {"installed": True, "signed_in": True, "subscription": "pro"}


def test_a_review_by_claude_code_runs_without_a_question(monkeypatch, tmp_path):
    video, work = project_folder(tmp_path)
    model = Reviewer([[fix(1, "We met Peter in the laboratory.")]])
    q, translated = a_queue(monkeypatch, tmp_path, [LOCAL, CLAUDE, CODE], model)
    t = a_task(q, tmp_path, video, review="full", translator="local")
    q._run(t)
    assert (t.state, t.waits) == (TaskState.PAUSED, "validation") and len(model.requests) == 1       # reviewed at once: only its lines wait
    assert t.estimate["review"]["engine"] == "code"


# --- the pauses of a task --------------------------------------------------------------------------------------------

def a_queue(monkeypatch, tmp_path, translators, model, active="local"):
    cfg = settings(tmp_path, translators, active)
    monkeypatch.setattr(config, "load", lambda: cfg)
    monkeypatch.setattr(translation, "create", lambda m, log=print: model)
    translated = []

    def translate(r, p, log, step, cancelled):
        translated.append([c.text for c in p.cues])
        return pipeline.Result(p.video, p.source_srt, p.source_srt, p.code, len(p.cues), True)
    monkeypatch.setattr(pipeline, "translate_and_copy", translate)
    return TaskQueue(history=[]), translated


def a_task(q, tmp_path, video, **options):
    file = tmp_path / "dl-sub-dub.project.json"
    return q.add({"source": str(video), "project": "L", "project_file": str(file), "glossary": str(file.with_name("subtitles.glossary.json")),
                  "languages": ["fr"], "check_names": True, **options})


def test_a_paid_review_waits_for_its_cost_then_for_its_proposals(monkeypatch, tmp_path):
    video, work = project_folder(tmp_path)
    model = Reviewer([[fix(1, "We met Peter in the laboratory.")]])
    q, translated = a_queue(monkeypatch, tmp_path, [LOCAL, CLAUDE], model)
    t = a_task(q, tmp_path, video, review="full", translator="local")
    q._run(t)
    assert (t.state, t.waits, t.step) == (TaskState.PAUSED, "review", "review") and not model.requests       # nothing spent yet
    r = q.estimate(t.id)["review"]
    assert (r["engine"], r["count"], r["mode"]) == ("claude", 4, "full") and r["translators"][1]["cost_usd"] > 0
    assert any("Estimation de la relecture IA" in line for line in t.log) and t.summary()["waits"] == "review"
    q.resume(t.id)
    q._run(t)
    assert (t.state, t.waits) == (TaskState.PAUSED, "validation") and len(model.requests) == 1 and not translated
    assert "review" not in q.estimate(t.id) and q.estimate(t.id)["waits"] == "validation"
    corrections.decide(work, [{"id": corrections.pending(work)[0]["id"], "accept": True}])
    q.resume(t.id)
    q._run(t)
    assert t.state == TaskState.DONE and translated == [["We met Peter in the laboratory.", "He works on the genome.", "Thank you for watching!", "The cells emit light."]]
    assert len(model.requests) == 1                        # not reviewed again once its corrections changed its subtitles


def test_a_local_review_runs_at_once_and_waits_only_for_its_proposals(monkeypatch, tmp_path):
    video, work = project_folder(tmp_path)
    model = Reviewer([[fix(3, "")]])
    q, translated = a_queue(monkeypatch, tmp_path, [LOCAL, CLAUDE], model)
    t = a_task(q, tmp_path, video, review="full", review_engine="local", translator="local")
    q._run(t)
    assert (t.state, t.waits) == (TaskState.PAUSED, "validation") and len(model.requests) == 1
    q.resume(t.id)                                         # no decision: nothing is fixed, and it is not asked twice
    q._run(t)
    assert t.state == TaskState.DONE and translated[0][2] == "Thank you for watching!"


def test_nothing_proposed_nothing_waits(monkeypatch, tmp_path):
    video, work = project_folder(tmp_path)
    model = Reviewer([[]])
    q, translated = a_queue(monkeypatch, tmp_path, [LOCAL], model)
    t = a_task(q, tmp_path, video, review="full", translator="local")
    q._run(t)
    assert t.state == TaskState.DONE and len(model.requests) == 1 and len(translated) == 1
    assert corrections.reviewed(work) and (work / review.VIDEO_FILE).is_file()


def test_one_go_ahead_covers_the_review_and_the_translation(monkeypatch, tmp_path):
    video, work = project_folder(tmp_path)
    model = Reviewer([[]])
    q, translated = a_queue(monkeypatch, tmp_path, [CLAUDE], model, active="claude")
    t = a_task(q, tmp_path, video, review="full", translator="claude")
    q._run(t)
    assert t.waits == "review" and q.estimate(t.id)["translators"][0]["cost_usd"] > 0       # both costs are shown
    q.resume(t.id)
    q._run(t)
    assert t.state == TaskState.DONE and len(translated) == 1


def test_only_spend_agreed_lets_a_paid_model_run_without_a_question(monkeypatch, tmp_path):
    video, work = project_folder(tmp_path)
    model = Reviewer([[fix(1, "We met Peter in the laboratory.")]])
    q, translated = a_queue(monkeypatch, tmp_path, [CLAUDE], model, active="claude")
    t = a_task(q, tmp_path, video, review="full", translator="claude", pause=False)       # "never pause", of older versions: it asks
    q._run(t)
    assert (t.state, t.waits) == (TaskState.PAUSED, "review") and not model.requests
    q.cancel(t.id)
    t = a_task(q, tmp_path, video, review="full", translator="claude", spend_agreed=True)     # the user said not to be asked any more
    q._run(t)
    assert (t.state, t.waits) == (TaskState.PAUSED, "validation") and len(model.requests) == 1     # its proposals still wait
    q.resume(t.id)
    q._run(t)
    assert t.state == TaskState.DONE and len(translated) == 1


def test_the_review_can_be_dropped_or_given_to_another_model_at_the_go_ahead(monkeypatch, tmp_path):
    video, work = project_folder(tmp_path)
    model = Reviewer([])
    q, translated = a_queue(monkeypatch, tmp_path, [LOCAL, CLAUDE], model)
    t = a_task(q, tmp_path, video, review="full", translator="local")
    q._run(t)
    assert t.waits == "review"
    q.resume(t.id, review={"review": "", "review_engine": None})
    q._run(t)
    assert t.state == TaskState.DONE and not model.requests and not corrections.reviewed(work)


def test_a_model_that_fails_does_not_stop_the_video(monkeypatch, tmp_path):
    video, work = project_folder(tmp_path)
    model = Reviewer([translation.TranslationError("serveur arrêté"), translation.TranslationError("serveur arrêté")])
    q, translated = a_queue(monkeypatch, tmp_path, [LOCAL], model)
    t = a_task(q, tmp_path, video, review="full", translator="local")
    q._run(t)
    assert t.state == TaskState.DONE and len(translated) == 1 and not corrections.reviewed(work)
    assert any("Relecture IA non faite" in line for line in t.log)


def test_the_names_alone_wait_for_their_cost_too(monkeypatch, tmp_path):
    video, work = project_folder(tmp_path)
    model = Reviewer([])
    q, translated = a_queue(monkeypatch, tmp_path, [LOCAL, CLAUDE], model)
    t = a_task(q, tmp_path, video, review=True, translator="local")       # True: a project written before the full review
    q._run(t)
    assert (t.state, t.waits) == (TaskState.PAUSED, "review") and not model.names       # a paid model: nothing sent without a go-ahead
    assert t.estimate["review"]["mode"] == "names" and t.estimate["review"]["count"] == 0
    q.resume(t.id)
    q._run(t)
    assert t.state == TaskState.DONE and len(model.names) == 1 and not model.requests


# --- in a project ----------------------------------------------------------------------------------------------------

def test_a_video_paused_again_after_the_go_ahead_waits_for_the_next_one(tmp_path):
    run = Run("r", [{"path": "a", "state": "prepared"}, {"path": "b", "state": "prepared"}], {}, tmp_path / "l.json", "L")
    run.phase = 2
    a, b = run.items()
    assert run.next_item() is a
    run.held.add(a.id)                                     # resumed, reviewed, paused again for its proposals
    assert run.next_item() is b
    run.held.add(b.id)
    assert run.next_item() is None
    run.end_phase()
    assert run.state == "awaiting_translation"


def test_a_project_stops_for_the_cost_then_for_the_lines_then_translates(monkeypatch, tmp_path):
    video, work = project_folder(tmp_path)
    model = Reviewer([[fix(1, "We met Peter in the laboratory."), fix(3, "", "low")]])
    q, translated = a_queue(monkeypatch, tmp_path, [LOCAL, CLAUDE], model)
    g = Manager(q, start=False)
    run = Run(str(tmp_path), [{"path": video.as_posix(), "state": "todo"}],
              {"translator": "local", "languages": ["fr"], "review": "full", "output_dir": str(tmp_path)}, tmp_path / "dl-sub-dub.project.json", "L")
    g.projects.all["L"] = run

    def turn():
        g.projects.feed()
        for t in list(q.tasks.values()):
            if t.state == TaskState.QUEUED:
                q._run(t)
        g.projects.feed()

    turn()
    e = g.projects.estimate("L")
    assert run.state == "awaiting_translation" and e["waits"] == {"language": 0, "review": 1, "validation": 0, "translation": 0}
    assert (e["review"]["videos"], e["review"]["engine"], e["review"]["count"]) == (1, "claude", 4) and not model.requests
    assert g.projects.translate("L", None, None, None, {"review_engine": "local"})        # the go-ahead, with another model
    turn()
    e = g.projects.estimate("L")
    assert run.state == "awaiting_translation" and e["waits"]["validation"] == 1 and e["review"] is None
    assert run.options["review_engine"] == "local" and len(model.requests) == 1 and not translated
    (lines,) = g.projects.proposals("L")["corrections"]
    assert lines["title"] == "Episode 1.mp4" and [c["line"] for c in lines["corrections"]] == [1, 3]
    assert not run.videos[0]["validated"]
    decisions = [{"item": lines["item"], "id": c["id"], "accept": c["confidence"] != "low"} for c in lines["corrections"]]
    assert g.projects.validate_names("L", [], decisions) == {"accepted": 0, "rejected": 0, "lines_accepted": 1, "lines_rejected": 1}
    assert run.videos[0]["validated"] and g.projects.proposals("L")["corrections"] == []
    assert g.projects.translate("L", None, None)
    turn()
    assert run.state == "done" and translated == [["We met Peter in the laboratory.", "He works on the genome.", "Thank you for watching!", "The cells emit light."]]


def test_the_review_can_be_taken_off_the_project_at_the_go_ahead(monkeypatch, tmp_path):
    video, work = project_folder(tmp_path)
    model = Reviewer([])
    q, translated = a_queue(monkeypatch, tmp_path, [LOCAL, CLAUDE], model)
    g = Manager(q, start=False)
    run = Run(str(tmp_path), [{"path": video.as_posix(), "state": "todo"}],
              {"translator": "local", "languages": ["fr"], "review": "full", "output_dir": str(tmp_path)}, tmp_path / "dl-sub-dub.project.json", "L")
    g.projects.all["L"] = run
    for _ in range(2):
        g.projects.feed()
        for t in list(q.tasks.values()):
            if t.state == TaskState.QUEUED:
                q._run(t)
    assert run.state == "awaiting_translation"
    assert g.projects.translate("L", None, None, None, {"review": ""})
    for _ in range(2):
        g.projects.feed()
        for t in list(q.tasks.values()):
            if t.state == TaskState.QUEUED:
                q._run(t)
    g.projects.feed()
    assert run.state == "done" and run.options["review"] == "" and not model.requests and len(translated) == 1


# --- fixes proposed from outside (an assistant, through the MCP server) -----------------------------------------------

def test_fixes_proposed_from_outside_wait_like_those_of_a_model(tmp_path):
    cues = cues_of()
    kept, refused = corrections.propose(tmp_path, cues, [fix(1, "We met Peter in the laboratory."), fix(2, "He works on the Genome!"),
                                                         fix(4, "The cells emit light. " * 3), fix(9, "x"), fix(1, "again"), {"line": "x"}, "junk"], "assistant")
    assert [c["line"] for c in kept] == [1] and kept[0]["status"] == "pending" and kept[0]["id"] == "c0"
    assert refused == [{"line": 2, "why": "unchanged"}, {"line": 4, "why": "too_long"}, {"line": 9, "why": "unknown_line"},
                       {"line": 1, "why": "twice"}, {"line": None, "why": "no_line"}]
    d = corrections.load(tmp_path)
    assert (d["lines"], d["done"], d["engine"]) == (4, 4, "assistant") and corrections.reviewed(tmp_path)      # read whole: not read again by a model
    kept, _ = corrections.propose(tmp_path, cues, [fix(1, "We met Peter in the lab."), fix(3, "")], "assistant")       # a line waiting takes the new proposal
    assert [c["corrected"] for c in corrections.pending(tmp_path)] == ["We met Peter in the lab.", ""]
    corrections.decide(tmp_path, [{"id": "c0", "accept": True}])
    kept, refused = corrections.propose(tmp_path, cues, [fix(1, "We met Peter in the library.")], "assistant")
    assert not kept and refused == [{"line": 1, "why": "decided"}] and corrections.accepted(tmp_path)[0]["corrected"] == "We met Peter in the lab."


def test_a_part_read_leaves_the_rest_to_the_review(tmp_path):
    corrections.propose(tmp_path, cues_of(), [fix(3, "")], "assistant", complete=False)
    assert len(corrections.pending(tmp_path)) == 1 and not corrections.reviewed(tmp_path)


def a_project(monkeypatch, tmp_path, state="done"):
    video, work = project_folder(tmp_path)
    q, translated = a_queue(monkeypatch, tmp_path, [LOCAL, CLAUDE], Reviewer([]))
    g = Manager(q, start=False)
    run = Run(str(tmp_path), [{"path": video.as_posix(), "state": state}], {"translator": "local", "languages": ["fr"], "output_dir": str(tmp_path)},
              tmp_path / "dl-sub-dub.project.json", "L")
    g.projects.all["L"] = run
    return g, run, work, run.items()[0].id


def test_an_assistant_reads_the_subtitles_and_proposes_fixes(monkeypatch, tmp_path):
    g, run, work, item = a_project(monkeypatch, tmp_path)
    s = g.projects.subtitles_of("L", item)
    assert (s["count"], s["waiting"], s["title"]) == (4, 0, "Episode 1.mp4")
    assert s["lines"][0] == {"line": 1, "start": 0.0, "end": s["lines"][0]["end"], "text": "We met Peter in the lavatory."}
    stamp = run.summary()["review_stamp"]
    r = g.projects.propose_corrections("L", item, [fix(1, "We met Peter in the laboratory."), fix(2, "He works on the genome")], "Claude")
    assert (r["proposed"], r["waiting"], r["state"]) == (1, 1, "done") and r["refused"] == [{"line": 2, "why": "unchanged"}]
    assert run.summary()["review_stamp"] == stamp + 1                         # the page reads the proposals again
    (lines,) = g.projects.proposals("L")["corrections"]
    assert lines["corrections"][0]["corrected"] == "We met Peter in the laboratory." and corrections.load(work)["engine"] == "Claude"
    g.projects.validate_names("L", [], [{"item": item, "id": lines["corrections"][0]["id"], "accept": True}])
    assert g.projects.subtitles_of("L", item)["lines"][0]["text"] == "We met Peter in the laboratory."       # as the step will cut them
    assert g.projects.subtitles_of("L", "nope") is None and g.projects.propose_corrections("nope", item, []) is None


def test_no_proposal_for_a_video_not_transcribed_or_being_subtitled(monkeypatch, tmp_path):
    g, run, work, item = a_project(monkeypatch, tmp_path, state="running")
    assert g.projects.propose_corrections("L", item, [fix(1, "We met Peter in the laboratory.")]) == {"transcribed": True, "busy": True}
    assert not corrections.pending(work)
    (work / "transcription.json").unlink()
    assert g.projects.subtitles_of("L", item) == {"transcribed": False}


def test_a_translation_is_read_line_by_line(monkeypatch, tmp_path):
    g, run, work, item = a_project(monkeypatch, tmp_path)
    assert g.projects.subtitles_of("L", item, "fr")["translated"] is False
    pipeline.translation_file(work, "fr").write_text(json.dumps(["Un.", "Deux.", "Trois.", "Quatre."]), encoding="utf-8")
    pipeline.translation_source_file(work, "fr").write_text(json.dumps([*SENTENCES[:3], "The cells emit lights."]), encoding="utf-8")
    s = g.projects.subtitles_of("L", item, "FR")
    assert (s["language"], s["translated"], s["stale"]) == ("fr", True, [4]) and s["lines"][1]["translation"] == "Deux."


def test_only_the_lines_whose_original_changed_are_translated_again(monkeypatch, tmp_path):
    video, work = project_folder(tmp_path)
    cfg = settings(tmp_path, [LOCAL])
    asked = []

    class Translator:
        at_once = 1

        def prepare(self):
            pass

        def complete(self, system, request, schema=None, effort=None):
            asked.append(request)
            lines = request.split("Lignes à traduire :\n")[1].splitlines()
            return "\n".join(f"{i + 1}. FR {line.split('. ', 1)[1]}" for i, line in enumerate(lines))

        def finish(self):
            pass
    monkeypatch.setattr(translation, "create", lambda m, log=print: Translator())
    r = pipeline.Request(source=str(video), cfg=cfg, languages=["fr"], translator_id="local", output_dir=str(tmp_path), cleanup=False)
    p = pipeline.prepare(r, log=lambda _m: None)
    said = []
    pipeline.translate_and_copy(r, p, log=said.append)
    assert len(asked) == 1 and json.loads(pipeline.translation_source_file(work, "fr").read_text(encoding="utf-8")) == SENTENCES
    assert pipeline.remaining_languages(p, ["fr"]) == []
    corrections.propose(work, p.cues, [fix(1, "We met Peter in the laboratory.")], "assistant")
    corrections.decide(work, [{"id": "c0", "accept": True}])
    p = pipeline.prepare(r, log=lambda _m: None)
    assert pipeline.remaining_languages(p, ["fr"]) == ["fr"]                   # a paid translator would ask first
    pipeline.translate_and_copy(r, p, log=said.append)
    assert len(asked) == 2 and "laboratory" in asked[1] and "genome" not in asked[1]         # that line alone
    fr = json.loads(pipeline.translation_file(work, "fr").read_text(encoding="utf-8"))
    assert fr[0] == "FR We met Peter in the laboratory." and fr[1] == "FR He works on the genome."
    assert any("1 ligne(s) dont l'original a changé" in line for line in said) and pipeline.remaining_languages(p, ["fr"]) == []
    pipeline.translate_and_copy(r, p, log=said.append)
    assert len(asked) == 2                                 # nothing more to translate


def test_a_translation_of_an_earlier_version_follows_the_fixes_decided_after_it(monkeypatch, tmp_path):
    """Made before its originals were kept (no translation.<language>.source.json), then a fix accepted: that line is
    translated again, found by cutting the subtitles without the fixes accepted."""
    video, work = project_folder(tmp_path)
    cfg = settings(tmp_path, [LOCAL])
    asked = []

    class Translator:
        at_once = 1

        def prepare(self):
            pass

        def complete(self, system, request, schema=None, effort=None):
            asked.append(request)
            lines = request.split("Lignes à traduire :\n")[1].splitlines()
            return "\n".join(f"{i + 1}. FR {line.split('. ', 1)[1]}" for i, line in enumerate(lines))

        def finish(self):
            pass
    monkeypatch.setattr(translation, "create", lambda m, log=print: Translator())
    r = pipeline.Request(source=str(video), cfg=cfg, languages=["fr"], translator_id="local", output_dir=str(tmp_path), cleanup=False)
    old = [f"FR {s}" for s in SENTENCES]
    pipeline.translation_file(work, "fr").write_text(json.dumps(old), encoding="utf-8")       # an earlier version: no originals kept
    p = pipeline.prepare(r, log=lambda _m: None)
    assert p.before is None and pipeline.remaining_languages(p, ["fr"]) == []
    corrections.propose(work, p.cues, [fix(1, "We met Peter in the laboratory.")], "assistant")
    corrections.decide(work, [{"id": "c0", "accept": True}])
    import os
    os.utime(pipeline.translation_file(work, "fr"), (1, 1))                                  # made before the decision
    p = pipeline.prepare(r, log=lambda _m: None)
    assert p.before == SENTENCES and pipeline.remaining_languages(p, ["fr"]) == ["fr"]
    pipeline.translate_and_copy(r, p, log=lambda _m: None)
    assert len(asked) == 1 and "laboratory" in asked[0] and "genome" not in asked[0]          # that line alone
    fr = json.loads(pipeline.translation_file(work, "fr").read_text(encoding="utf-8"))
    assert fr[0] == "FR We met Peter in the laboratory." and fr[1:] == old[1:]
    assert pipeline.remaining_languages(pipeline.prepare(r, log=lambda _m: None), ["fr"]) == []    # its originals are kept now
