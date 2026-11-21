"""Full AI review of a video: the model reads every subtitle of the spoken language, in order, and proposes fixes line
by line (a word heard wrong, a number, a sentence made up during a silence); nothing is fixed without a decision.

File `corrections.json` in the working folder of the video:
{"lines": subtitles read, "done": how many of them were reviewed (an interrupted review goes on from there),
 "engine": name of the model, "corrections": [{id, line, start, end, original, corrected, reason, confidence, status}]}.
`status`: "pending" until decided, then "accepted" or "rejected"; `auto`: accepted without a question, because the
project asked for it and the model said the fix sure (accept_sure). `start` and `end` are the times of the first and last
word of the subtitle in `transcription.json`, which is never rewritten: an accepted correction replaces the words of
that span in memory (`apply`, called by `pipeline.prepare` before the glossary and the segmentation), so it holds
when the subtitles are cut again otherwise. An empty `corrected` removes the words (a sentence that was never said).
The instructions sent to the model are written in French; its reasons are asked in the language of the interface.
"""
import json
import re
from pathlib import Path

from . import config, i18n, translation

FILE_NAME = "corrections.json"
BLOCK_LINES = {"claude": 800, "claude_code": 800, "openai": 120}   # subtitles sent per request (`review_lines` of a translator changes it):
                                               # a whole episode at once for Claude (613 subtitles: 13 600 tokens, 17/11/2026)
EARLIER_MAX = 25                               # fixes of the blocks before, given again so that the same mistake is fixed the same way
CONTEXT_LINES = 8                              # subtitles before a block, given again as context
TOLERANCE_S = 0.005
CONFIDENCES = ("high", "medium", "low")
REASON_MAX = 240                               # characters kept of a reason (a small model sometimes thinks aloud in it)

INSTRUCTIONS = """Tu relis les sous-titres d'une transcription automatique (reconnaissance vocale), dans leur langue d'origine, avant leur traduction.
La reconnaissance vocale se trompe sur ce qu'elle connaît mal : elle écrit un mot courant qui sonne comme le mot rare, déforme les noms propres, les sigles, les chiffres et les dates, et invente parfois une phrase pendant un silence, de la musique ou des applaudissements.

On te donne le titre de la vidéo, les noms déjà vérifiés, puis les sous-titres numérotés, dans l'ordre. Lis-les tous pour comprendre le sujet, puis ne renvoie QUE les lignes à corriger :
- un mot ou une expression mal entendus, que le contexte permet de rétablir (mot qui sonne pareil, terme technique, nom propre ou sigle déformé) ;
- un chiffre, une date ou une unité manifestement faux d'après le reste du discours ;
- une phrase qui n'a pas été dite : sans rapport avec le discours, dans une autre langue que le reste, ou répétée en boucle. Pour la supprimer, « corrected » vide.

Règles :
- Ne réécris pas le style. Ne corrige ni la grammaire de l'oral, ni les hésitations, ni les répétitions, ni la seule ponctuation. Ne traduis rien.
- Un mot inventé par l'orateur, de l'argot, un mélange de langues voulu ou un jeu de mots se gardent tels quels.
- Garde le reste de la ligne mot pour mot : seuls les mots fautifs changent. Ne déplace aucun mot d'une ligne à l'autre, ne fusionne pas de lignes.
- Les noms déjà vérifiés s'écrivent comme dans la liste.
- Ne change pas une ligne pour sa seule ponctuation, ses majuscules ou un trait d'union. Ne remplace pas une expression familière par une autre plus courante.
- Un chiffre ou une date ne se corrige que si une autre ligne de la vidéo le contredit, jamais d'après tes connaissances.
- Ne devine pas. Sans indice dans le contexte, ne propose rien ; un doute sérieux qui mérite d'être signalé se propose avec « confidence » à « low ». Si ta raison contient « peut-être », « probablement » ou une alternative, ce n'est pas « high ».
- La plupart des lignes sont justes : une réponse vide est normale.

Pour chaque ligne à corriger : « line » = son numéro, « corrected » = la ligne entière corrigée, « reason » = une phrase courte, écrite en {langue}, « confidence » = high, medium ou low."""

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["corrections"], "properties": {"corrections": {"type": "array", "items": {
    "type": "object", "additionalProperties": False, "required": ["line", "corrected", "reason", "confidence"],
    "properties": {"line": {"type": "integer"}, "corrected": {"type": "string"}, "reason": {"type": "string"},
                   "confidence": {"type": "string", "enum": list(CONFIDENCES)}}}}}}


def path_for(work: str | Path) -> Path:
    return Path(work) / FILE_NAME


def load(work: str | Path | None) -> dict:
    """The review of a video; empty without a file or if it is unreadable (the work goes on without corrections)."""
    empty = {"lines": 0, "done": 0, "engine": "", "corrections": []}
    if not work:
        return empty
    try:
        d = json.loads(path_for(work).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return empty
    if not isinstance(d, dict):
        return empty
    return {**empty, **d, "corrections": [c for c in d.get("corrections") or [] if isinstance(c, dict) and c.get("id")]}


def _write(work: Path, d: dict):
    f = path_for(work)
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f.name + ".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(f)


def fingerprint(work: str | Path | None) -> tuple:
    """The corrections accepted, as they are now: one more (or one retouched) since the segmentation forces it to be
    redone before translating."""
    return tuple((c["id"], c.get("corrected")) for c in accepted(work))


def forget(work: str | Path):
    """The transcription is made again: the corrections of the one before no longer hold."""
    path_for(work).unlink(missing_ok=True)


def reviewed(work: str | Path | None) -> bool:
    """The whole video was reviewed: it is not reviewed twice, even once its corrections changed its subtitles."""
    d = load(work)
    return d["lines"] > 0 and d["done"] >= d["lines"]


def pending(work: str | Path | None) -> list[dict]:
    return [c for c in load(work)["corrections"] if c.get("status") == "pending"]


def accepted(work: str | Path | None) -> list[dict]:
    return [c for c in load(work)["corrections"] if c.get("status") == "accepted"]


def block_lines(m: dict) -> int:
    """Subtitles sent per request: a remote model reads a whole episode at once or nearly, a local one smaller pieces."""
    try:
        n = int(m.get("review_lines") or 0)
    except (TypeError, ValueError):
        n = 0
    return max(20, n) if n > 0 else BLOCK_LINES.get(m.get("type", "openai"), 120)


def _span(cue) -> tuple[float, float]:
    """Times of the first and last word of a subtitle (those of the transcription, not the display times)."""
    words = getattr(cue, "words", None)
    return (float(words[0].start), float(words[-1].end)) if words else (float(cue.start), float(cue.end))


def _request(title: str, names: list[str], texts: list[str], first: int, last: int, earlier: list[dict] | None = None) -> str:
    parts = [f"Titre : {title}"]
    if names:
        parts.append("Noms déjà vérifiés : " + ", ".join(names))
    if earlier:
        parts.append("Corrections déjà proposées plus haut dans cette vidéo (la même erreur se corrige de la même façon) :\n"
                     + "\n".join(f"- « {c['original']} » → « {c['corrected']} »" for c in earlier[-EARLIER_MAX:]))
    context = range(max(0, first - CONTEXT_LINES), first)
    if context:
        parts.append("Contexte, les lignes précédentes (déjà relues : ne pas les corriger) :\n" + "\n".join(f"{i + 1}. {texts[i]}" for i in context))
    parts.append("Lignes à relire :\n" + "\n".join(f"{i + 1}. {texts[i]}" for i in range(first, last)))
    return "\n\n".join(parts)


def _ask(translator, system: str, request: str) -> list[dict]:
    """The corrections of one block; an answer that cannot be read is asked once more (local models)."""
    for attempt in (1, 2):
        try:
            effort = getattr(translator, "review_effort", None) or "medium"       # chosen for this translator (Claude)
            found = translation.parse_json(translator.complete(system, request, schema=SCHEMA, effort=effort)).get("corrections")
            if isinstance(found, list):
                return [e for e in found if isinstance(e, dict)]
        except translation.TranslationError:
            if attempt == 2:
                raise
    raise translation.TranslationError(i18n.t("err.answer_not_json", answer="corrections"))


def _plain(text: str) -> str:
    """A subtitle without its punctuation, capitals and hyphens: two that only differ by them say the same."""
    return " ".join(re.sub(r"[\W_]+", " ", text.lower()).split())


def _entries(found: list[dict], cues: list, first: int, last: int, refused: list | None = None) -> list[dict]:
    """The answers kept: a line of the block, once, really changed (not only its punctuation or capitals), and not grown
    by the words of its neighbours (a model that merges two lines). `refused` receives the others: {line, why}."""
    result, seen = [], set()

    def refuse(line, why):
        if refused is not None:
            refused.append({"line": line, "why": why})

    for e in found:
        try:
            line = int(e.get("line"))
        except (TypeError, ValueError, AttributeError):
            refuse(None, "no_line")
            continue
        corrected = " ".join(str(e.get("corrected") or "").split())
        if not first < line <= last or line in seen:
            refuse(line, "twice" if line in seen else "unknown_line")
            continue
        original = cues[line - 1].text
        if _plain(corrected) == _plain(original) or len(corrected.split()) > len(original.split()) * 1.5 + 2:
            refuse(line, "unchanged" if _plain(corrected) == _plain(original) else "too_long")
            continue
        seen.add(line)
        start, end = _span(cues[line - 1])
        result.append({"id": f"c{round(start * 1000)}", "line": line, "start": round(start, 3), "end": round(end, 3),
                       "original": cues[line - 1].text, "corrected": corrected, "reason": " ".join(str(e.get("reason") or "").split())[:REASON_MAX],
                       "confidence": e.get("confidence") if e.get("confidence") in CONFIDENCES else "medium", "status": "pending"})
    return result


def review(m: dict, work: Path, title: str, cues: list, names: list[str] | None = None, log=print, cancelled=lambda: False,
           create=None, progress=None) -> int:
    """Reviews the subtitles `cues` of a video with the translator `m`; returns the number of corrections waiting for a
    decision. Block by block: what is reviewed is written at once, so a failure or a stop goes on from there next time."""
    work = Path(work)
    d = load(work)
    if d["lines"] > 0 and d["done"] >= d["lines"]:              # reviewed already
        return sum(1 for c in d["corrections"] if c.get("status") == "pending")
    if d["lines"] != len(cues):                                 # nothing yet, or cut otherwise since it was interrupted: from the start
        decided = [c for c in d["corrections"] if c.get("status") != "pending"]
        d = {"lines": len(cues), "done": 0, "engine": "", "corrections": decided}
    texts = [c.text for c in cues]
    size = block_lines(m)
    if d["done"] < len(cues):
        log(i18n.t("log.review_lines", n=len(cues) - d["done"], name=config.engine_name(m)))
        system = INSTRUCTIONS.format(langue=i18n.language_name(i18n.language()))
        translator = (create or translation.create)(m, log)
        try:
            translator.prepare()
            while d["done"] < len(cues):
                if cancelled():
                    raise translation.TranslationError(i18n.t("err.cancelled"))
                first, last = d["done"], min(len(cues), d["done"] + size)
                earlier = [c for c in d["corrections"] if c.get("corrected") and c.get("status") != "rejected"] if first else []
                new = _entries(_ask(translator, system, _request(title, names or [], texts, first, last, earlier)), cues, first, last)
                known = {c["id"] for c in d["corrections"]}
                d["corrections"] += [c for c in new if c["id"] not in known]
                d["done"], d["engine"] = last, config.engine_name(m)
                _write(work, d)
                if progress:
                    progress(last, len(cues))
        finally:
            translator.finish()
        log(i18n.t("log.review_lines_to_check", n=sum(1 for c in d["corrections"] if c.get("status") == "pending")))
    return sum(1 for c in d["corrections"] if c.get("status") == "pending")


def propose(work: str | Path, cues: list, found: list[dict], by: str, complete: bool = True) -> tuple[list[dict], list[dict]]:
    """Corrections proposed from outside (an assistant, through the MCP server) for the subtitles `cues` of a video, kept
    under the same rules as those of a model and decided in the page like them. Returns (those kept, those refused:
    {line, why}). A line already waiting takes the new proposal; one already decided stays decided. `complete`: the
    whole video was read, so the AI review of the project will not read it again."""
    work = Path(work)
    d = load(work)
    if d["lines"] != len(cues):                                 # nothing yet, or cut otherwise since: what was decided stays
        d = {"lines": len(cues), "done": 0, "engine": "", "corrections": [c for c in d["corrections"] if c.get("status") != "pending"]}
    refused: list[dict] = []
    new = _entries([e for e in found if isinstance(e, dict)], cues, 0, len(cues), refused)
    decided = {c["id"] for c in d["corrections"] if c.get("status") != "pending"}
    kept = [c for c in new if c["id"] not in decided]
    refused += [{"line": c["line"], "why": "decided"} for c in new if c["id"] in decided]
    replaced = {c["id"] for c in kept}
    d["corrections"] = [c for c in d["corrections"] if c["id"] not in replaced] + kept
    if complete:
        d["done"] = len(cues)
    if kept or complete:
        d["engine"] = by
    _write(work, d)
    return kept, refused


def decide(work: str | Path, decisions: list[dict]) -> tuple[int, int]:
    """Applies the decisions {id, accept, corrected?} to the corrections waiting. Returns (accepted, rejected). A text
    retouched replaces the proposal; accepted without any change to the subtitle, it brings nothing: rejected."""
    d = load(work)
    by_id = {c["id"]: c for c in d["corrections"] if c.get("status") == "pending"}
    yes, no = 0, 0
    for x in decisions:
        c = by_id.pop(x.get("id"), None)
        if c is None:
            continue
        if "corrected" in x and x["corrected"] is not None:
            c["corrected"] = " ".join(str(x["corrected"]).split())
        if x.get("accept") and c["corrected"] != c["original"]:
            c["status"] = "accepted"
            yes += 1
        else:
            c["status"] = "rejected"
            no += 1
    if yes or no:
        _write(Path(work), d)
    return yes, no


def accept_sure(work: str | Path) -> int:
    """The project asked for the sure fixes to be applied without a question (`review_auto`): accepts the corrections
    waiting that the model says sure ("high"), marked `auto`. Never a removed line (a sentence said never said: nobody
    would see it gone). The others stay waiting, not applied. Returns how many were accepted."""
    d = load(work)
    sure = [c for c in d["corrections"] if c.get("status") == "pending" and c.get("confidence") == "high"
            and c.get("corrected") and c["corrected"] != c.get("original")]
    for c in sure:
        c["status"], c["auto"] = "accepted", True
    if sure:
        _write(Path(work), d)
    return len(sure)


def applied_alone(work: str | Path | None) -> list[dict]:
    """The corrections applied without a question (see accept_sure), still applied."""
    return [c for c in accepted(work) if c.get("auto")]


def take_back(work: str | Path, ids: list[str]) -> int:
    """Takes back corrections applied without a question: rejected, the line is its original again when the subtitles
    are next cut. Returns how many were taken back."""
    d = load(work)
    wanted = set(ids)
    back = [c for c in d["corrections"] if c["id"] in wanted and c.get("status") == "accepted" and c.get("auto")]
    for c in back:
        c["status"] = "rejected"
    if back:
        _write(Path(work), d)
    return len(back)


def _spread(piece: list[dict], tokens: list[str]) -> list[dict]:
    """The words of a corrected subtitle over the time of the words they replace: as many words, each keeps its
    times; otherwise the time is shared out by their length."""
    if len(tokens) == len(piece):
        return [{**w, "word": t} for w, t in zip(piece, tokens)]
    start, end = float(piece[0]["start"]), float(piece[-1]["end"])
    total = sum(len(t) + 1 for t in tokens)
    out, at = [], start
    for t in tokens:
        until = at + (end - start) * (len(t) + 1) / total
        out.append({**piece[0], "word": t, "start": round(at, 3), "end": round(until, 3)})
        at = until
    out[-1]["end"] = piece[-1]["end"]
    return out


def apply(words: list[dict], corrections: list[dict]) -> tuple[list[dict], int]:
    """Replaces, in the words of a transcription, those of each corrected subtitle. Returns (words, corrections
    applied); a correction whose words are not found (another transcription) is left out."""
    todo = sorted((c for c in corrections if c.get("corrected") is not None), key=lambda c: c["start"])
    if not todo:
        return words, 0
    out, i, count = [], 0, 0
    for c in todo:
        lo, hi = float(c["start"]) - TOLERANCE_S, float(c["end"]) + TOLERANCE_S
        while i < len(words) and float(words[i]["start"]) < lo:
            out.append(words[i])
            i += 1
        j = i
        while j < len(words) and float(words[j]["end"]) <= hi:
            j += 1
        if j == i:
            continue
        tokens = str(c["corrected"]).split()
        if tokens:
            out += _spread(words[i:j], tokens)
        i = j
        count += 1
    return out + words[i:], count
