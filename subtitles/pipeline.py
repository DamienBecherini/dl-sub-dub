"""The whole chain, shared by the command line and the web interface.

Output folder `output_dir`: the downloaded (YouTube) or copied (drag and drop) video; the intermediate files (extracted
audio, transcription.json, subtitles, translation.<language>.json, .vtt) in `output_dir/.work/<name>/`.
Files produced: `<name>.<original language>.srt` and one `<name>.<target>.srt` per target language, copied next to
the video and/or into the output folder (`[destination]` table), then cleanup of the working folder (`[cleanup]`).

Two phases: `prepare` (source, audio, transcription, original subtitles) then `translate_and_copy`
(translations, copies, cleanup); `run` chains both.
"""
import dataclasses
import hashlib
import json
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import cleanup, config, corrections, dubbing, formats, glossary, i18n, language, local_transcription, muxing, segmentation, source, steps, transcription, translation
from .segmentation import Cue, Settings

STEPS = ["source", "transcription", "segmentation", "review", "translation", "files", "dubbing", "mux"]
DEFAULT_DESTINATION = {"next_to_video": True, "output_dir": False}
FINGERPRINTS_FILE = "placed-subtitles.json"   # in the working folder: fingerprint of each .srt as it was placed


class Cancelled(Exception):
    pass


@dataclass
class Request:
    source: str                        # URL or path
    cfg: dict
    transcription_id: str | None = None
    translator_id: str | None = None
    language: str | None = None        # spoken language; None = setting, "" = detection
    output_dir: str | None = None
    redo: bool = False
    on_work: object = None             # callback called with the .work/<name> folder as soon as it is known (log)
    languages: list[str] | None = None   # target languages; None = cfg["target_languages"]
    destination: dict | None = None    # {"next_to_video": bool, "output_dir": bool}; None = cfg["destination"]
    cleanup: bool = True               # applies [cleanup] to the working folder at the end
    on_duration: object = None         # callback called with the audio duration before transcribing it (time left)
    glossary: str | None = None        # file of the validated names (see glossary.py)
    on_progress: object = None         # callback (done, total, languages left after this one) at each chunk translated


@dataclass
class OutputFile:
    language: str
    path: Path                         # main location (first copy, otherwise the working folder)
    count: int
    translated: bool
    copies: list[Path] = field(default_factory=list)


@dataclass
class Prepared:
    """Result of the first phase (everything that does not depend on the translator)."""
    video: Path
    work: Path
    base: str
    code: str                          # spoken language
    cues: list[Cue]
    source_srt: Path
    output_dir: Path
    start: float
    before: list[str] | None = None    # the subtitles cut without the fixes accepted (see translation_of); None: no fix accepted
    doubt: dict | None = None          # the transcription looks little like its language (language.doubt): {code, share, words}


@dataclass
class Result:
    video: Path
    srt: Path                          # main file: first target language, or original language
    source_srt: Path
    language: str
    cue_count: int
    translated: bool
    copies: list[Path] = field(default_factory=list)
    duration_s: float = 0.0
    files: list[OutputFile] = field(default_factory=list)


def target_languages(r: Request) -> list[str]:
    return language.normalize_codes(r.languages if r.languages is not None else r.cfg.get("target_languages", ["fr"]))


def destination(r: Request) -> dict:
    return {**DEFAULT_DESTINATION, **(r.cfg.get("destination") or {}), **(r.destination or {})}


def duration_text(seconds: float) -> str:
    s = round(seconds)
    h, m, s = s // 3600, s % 3600 // 60, s % 60
    return f"{h} h {m:02d} min" if h else f"{m} min {s:02d} s" if m else f"{s} s"


def transcription_timeout(m: dict, duration_s: float) -> int:
    """Longest wait for the answer: 4 times the expected time, at least 5 min; 2 h if the speed is not given."""
    try:
        speed = float(m.get("speed") or 0)
    except (TypeError, ValueError):
        speed = 0
    return max(300, round(4 * duration_s / speed)) if speed > 0 else 7200


def transcription_message(m: dict, duration_s: float) -> str:
    """Expected time from the speed of the chosen server (`speed` field) and the audio duration."""
    audio = duration_text(duration_s)
    try:
        speed = float(m.get("speed") or 0)
    except (TypeError, ValueError):
        speed = 0
    if speed <= 0:
        return i18n.t("log.transcribing_unknown_speed", audio=audio, name=config.engine_name(m))
    return i18n.t("log.transcribing", eta=duration_text(duration_s / speed), audio=audio, name=config.engine_name(m), speed=f"{speed:g}")


def translation_file(work: Path, target: str) -> Path:
    return work / f"translation.{target}.json"


def translation_source_file(work: Path, target: str) -> Path:
    """The subtitles a translation was made from, line by line: a line whose original changed since (a fix of the AI
    review or a name validated after the translation) is translated again, alone."""
    return work / f"translation.{target}.source.json"


def translation_of(work: Path, target: str, texts: list[str], before: list[str] | None = None) -> tuple[list[str] | None, list[int]]:
    """The translation kept of these subtitles into `target`, and the indexes of its lines to translate again (their
    original changed since). (None, []) without a translation of this segmentation. Its originals are those kept with
    it; for a translation of an earlier version, which kept none, made before the last fix decided: `before`, the
    subtitles cut without the fixes accepted."""
    def read(f: Path):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return d if isinstance(d, list) and len(d) == len(texts) else None
    translated = read(translation_file(work, target))
    if translated is None:
        return None, []
    made_from = read(translation_source_file(work, target))
    if made_from is None and before is not None and len(before) == len(texts) and _older(translation_file(work, target), corrections.path_for(work)):
        made_from = before
    return translated, ([i for i, (a, b) in enumerate(zip(made_from, texts)) if a != b] if made_from is not None else [])


def _older(a: Path, b: Path) -> bool:
    try:
        return a.stat().st_mtime < b.stat().st_mtime
    except OSError:
        return False


def remaining_languages(p: Prepared, languages: list[str], redo: bool = False) -> list[str]:
    """Languages really to translate: neither the spoken language, nor those whose translation of these subtitles is
    kept (same segmentation, no line whose original changed since)."""
    texts = [getattr(s, "text", "") for s in p.cues]

    def done(target: str) -> bool:
        translated, changed = translation_of(p.work, target, texts, getattr(p, "before", None))
        return translated is not None and not changed
    return [c for c in languages if c != p.code and (redo or not done(c))]


def work_dir(output_dir: str | Path, video: str | Path) -> Path:
    return Path(output_dir) / ".work" / source.slug(Path(video).stem)


def cut(words: list[dict], work: Path, glossary_file, settings: Settings, log=lambda _m: None, fixes: bool = True) -> list:
    """The subtitles of a transcription, in its language: its words, fixed by the lines of the full AI review that were
    accepted (unless `fixes` is false) and by the names of the glossary, then cut."""
    words, corrected = corrections.apply(words, corrections.accepted(work) if fixes else [])
    if corrected:
        log(i18n.t("log.corrections_applied", n=corrected))
    words, fixes = glossary.fix_words(words, glossary.load(glossary_file))
    if fixes:
        log(i18n.t("log.glossary_fixed", n=fixes))
    return segmentation.build(segmentation.from_json(words), settings)


def kept_cues(cfg: dict, work: str | Path, glossary_file=None, fixes: bool = True) -> list | None:
    """The subtitles in the original language of a video already transcribed, as the Subtitles step cuts them now
    (without the fixes accepted if `fixes` is false); None without a transcription."""
    try:
        words = json.loads((Path(work) / "transcription.json").read_text(encoding="utf-8"))["words"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return cut(words, Path(work), glossary_file, Settings(**cfg.get("subtitles", {})), fixes=fixes) if words else None


def transcription_kept(output_dir: str | Path, video: str | Path) -> bool:
    """Transcription already done: `prepare` will take it back in a few seconds, without a transcription server."""
    return (work_dir(output_dir, video) / "transcription.json").is_file()


def run(r: Request, log=print, step=lambda name: None, cancelled=lambda: False) -> Result:
    p = prepare(r, log, step, cancelled)
    return translate_and_copy(r, p, log, step, cancelled)


def prepare(r: Request, log=print, step=lambda name: None, cancelled=lambda: False) -> Prepared:
    cfg = r.cfg
    settings = Settings(**cfg.get("subtitles", {}))
    output_dir = Path(r.output_dir or cfg["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    m_tr = config.engine(cfg, "transcriptions", "active_transcription", r.transcription_id)
    wanted_language = (r.language if r.language is not None else cfg.get("language", "")) or None
    t_start = time.perf_counter()

    def check():
        if cancelled():
            raise Cancelled()

    # 1. Source
    step("source")
    if source.is_url(r.source):
        try:
            video = source.download_youtube(r.source, source.ytdlp_command(cfg), log, folder=source.download_dir(cfg, r.output_dir),
                                            cancelled=cancelled)
        except source.DownloadCancelled:
            raise Cancelled() from None
    else:
        video = Path(r.source).expanduser().resolve()
        if not video.is_file():
            raise source.SourceError(i18n.t("err.file_not_found", path=video))
    base = source.slug(video.stem)
    work = work_dir(output_dir, video)
    work.mkdir(parents=True, exist_ok=True)
    if r.on_work:
        r.on_work(work)
    tr_file = work / "transcription.json"
    audios = [p for p in work.glob(f"{base}.*") if source.is_extracted_audio(p, base)]
    if tr_file.exists() and not r.redo and not audios:
        audio = None                                   # transcription kept, audio cleaned: nothing to extract
    elif audios:
        audio = audios[0]
        log(i18n.t("log.audio_already", file=audio.name))
    else:
        log(i18n.t("log.audio_extracting", file=video.name))
        audio = source.extract_audio(video, work / f"{base}.audio")
    if audio:
        log(i18n.t("log.audio", file=audio.name, mb=f"{audio.stat().st_size / 1e6:.1f}", s=f"{source.duration(audio):.0f}", work=work))
    check()

    # 2. Transcription
    step("transcription")
    if tr_file.exists() and not r.redo:
        tr = json.loads(tr_file.read_text(encoding="utf-8"))
        log(i18n.t("log.transcription_already", n=len(tr["words"])))
    else:
        if m_tr.get("command"):
            local_transcription.LocalServer.for_engine(m_tr).ensure(log)
        log(i18n.t("log.transcription_by", name=config.engine_name(m_tr), url=m_tr["url"], state=transcription.health(m_tr["url"])))
        audio_duration = source.duration(audio)
        if r.on_duration:
            r.on_duration(audio_duration)
        log(transcription_message(m_tr, audio_duration))
        t = time.perf_counter()
        key = config.resolve_key(m_tr.get("key", ""))
        tr_language = wanted_language if wanted_language != "fr" else None
        timeout = transcription_timeout(m_tr, audio_duration)
        def ask():
            return transcription.transcribe(m_tr["url"], audio, tr_language, timeout_s=timeout, key=key, cancelled=cancelled)
        try:
            try:
                tr = ask()
            except transcription.TranscriptionTimeout as e:
                log(i18n.t("log.retry", error=e))      # answer lost on the way (already seen): a project must not stay stuck
                tr = ask()
        except transcription.TranscriptionCancelled:
            raise Cancelled() from None            # the server was told by the closed connection
        tr_file.write_text(json.dumps(tr, ensure_ascii=False, indent=1), encoding="utf-8")
        corrections.forget(work)               # they were those of the transcription before
        log(i18n.t("log.transcribed", s=round(time.perf_counter() - t), words=len(tr["words"]), segments=len(tr["segments"])))
    if not tr["words"]:
        raise transcription.TranscriptionError(i18n.t("err.no_speech"))
    check()

    # 3. Segmentation in the original language
    step("segmentation")
    code = wanted_language or tr.get("language") or language.detect(tr.get("text", "")) or _asked_language(work) or "xx"
    cues = cut(tr["words"], work, r.glossary, settings, log)      # with the lines fixed by the full review, and the glossary
    before = [c.text for c in cut(tr["words"], work, r.glossary, settings, fixes=False)] if corrections.accepted(work) else None
    source_srt = work / f"{base}.{code}.srt"
    source_srt.write_text(formats.srt(cues, settings), encoding="utf-8")
    log(i18n.t("log.source_subtitles", code=code, lang=i18n.language_name(code), n=len(cues), file=source_srt.name))
    doubt = language.doubt(tr.get("text") or " ".join(str(w.get("word", "")) for w in tr["words"]), code)
    if doubt:                                  # a language the engine does not know, written by ear: said before anything is spent on it
        log(i18n.t("log.language_doubt" if doubt["code"] else "log.language_doubt_none", share=round(doubt["share"] * 100),
                   lang=i18n.language_name(code), words=doubt["words"], usual=round(doubt["usual"] * 100)))
    return Prepared(video, work, base, code, cues, source_srt, output_dir, t_start, before, doubt)


def _fingerprint(f: Path) -> str:
    return hashlib.sha256(f.read_bytes()).hexdigest()


def place_srt(source_srt: Path, target: Path, fingerprints: dict, redo: bool = False, log=print):
    """Copies a .srt to its destination, unless the file there was modified since it was created (fixed by hand).
    A different file without a known fingerprint is kept too: when in doubt, nothing is overwritten. "Redo" replaces everything."""
    new = _fingerprint(source_srt)
    if target.exists() and not redo:
        current = _fingerprint(target)
        if current == new:
            fingerprints[str(target)] = new
            return
        if current != fingerprints.get(str(target)):
            log(i18n.t("log.subtitles_kept", file=target.name))
            return
    shutil.copyfile(source_srt, target)
    fingerprints[str(target)] = new


ASKED_LANGUAGE = "language.json"             # in the working folder: the language found by the translator (not asked again)


def _asked_language(work: Path) -> str:
    try:
        return str(json.loads((work / ASKED_LANGUAGE).read_text(encoding="utf-8")).get("code") or "")
    except (OSError, ValueError, AttributeError):
        return ""


def _recognised(p: Prepared, translator, name: str, log) -> Prepared:
    """Spoken language not found by its function words ("xx"): asked to the translator, which reads the subtitles
    anyway. Found, the subtitles of the spoken language take its code (file renamed) and it is kept for next time."""
    try:
        code = language.ask(translator, [c.text for c in p.cues])
    except translation.TranslationError as e:
        log(i18n.t("log.language_ask_failed", error=e))
        return p
    if not code:
        log(i18n.t("log.language_ask_failed", error="?"))
        return p
    source = p.source_srt.with_name(f"{p.base}.{code}.srt")
    p.source_srt.replace(source)
    (p.work / ASKED_LANGUAGE).write_text(json.dumps({"code": code}), encoding="utf-8")
    log(i18n.t("log.language_asked", translator=name, code=code, lang=i18n.language_name(code)))
    return dataclasses.replace(p, code=code, source_srt=source)


def translate_and_copy(r: Request, p: Prepared, log=print, step=lambda name: None, cancelled=lambda: False) -> Result:
    cfg = r.cfg
    settings = Settings(**cfg.get("subtitles", {}))
    m_trans = config.engine(cfg, "translators", "active_translator", r.translator_id)
    targets = [c for c in target_languages(r) if c != p.code]
    files = [OutputFile(p.code, p.source_srt, len(p.cues), False)]

    # 4. Translations, one per target language
    step("translation")
    if m_trans.get("type", "none") == "none" or not targets:
        log(i18n.t("log.no_translation"))
    else:
        texts = [s.text for s in p.cues]
        translator = None
        try:
            if p.code == "xx":                    # spoken language unknown: the translator recognises it
                translator = translation.create(m_trans, log)
                p = _recognised(p, translator, config.engine_name(m_trans), log)
                targets = [c for c in targets if c != p.code]
                files = [OutputFile(p.code, p.source_srt, len(p.cues), False)]
            for k, target in enumerate(targets):
                trans_file = translation_file(p.work, target)
                translated, changed = None, []
                if trans_file.exists() and not r.redo:
                    translated, changed = translation_of(p.work, target, texts, p.before)
                    if translated is None:
                        log(i18n.t("log.translation_other_split", lang=target))
                    elif not changed:
                        log(i18n.t("log.translation_already", lang=target))
                chunk_size = int(cfg.get("translation", {}).get("chunk_size", 25))
                if translated is not None and changed:        # only the lines whose original changed since (a fix accepted later)
                    if translator is None:
                        translator = translation.create(m_trans, log)
                    log(i18n.t("log.translating_changed", n=len(changed), lang=target, translator=config.engine_name(m_trans)))
                    again = [texts[i] for i in changed]
                    new = translation.translate_texts(translator, again, chunk_size, log, cancelled=cancelled, target=target,
                                                      terms=glossary.terms_for(glossary.load(r.glossary), again, target))
                    for i, text in zip(changed, new):
                        translated[i] = text
                    trans_file.write_text(json.dumps(translated, ensure_ascii=False, indent=1), encoding="utf-8")
                    translation_source_file(p.work, target).write_text(json.dumps(texts, ensure_ascii=False, indent=1), encoding="utf-8")
                if translated is None:
                    if translator is None:
                        translator = translation.create(m_trans, log)
                    log(i18n.t("log.translating", src=p.code, lang=target, name=i18n.language_name(target), translator=config.engine_name(m_trans), n=len(texts)))
                    t = time.perf_counter()
                    terms = glossary.terms_for(glossary.load(r.glossary), texts, target)
                    if terms:
                        log(i18n.t("log.glossary_terms", n=len(terms)))
                    translated = translation.translate_texts(translator, texts, chunk_size,
                                                             log, cancelled=cancelled, target=target, terms=terms,
                                                             progress=(lambda d, n, more=len(targets) - k - 1: r.on_progress(d, n, more))   # noqa: B008 (counted now, on purpose)
                                                             if r.on_progress else None)
                    trans_file.write_text(json.dumps(translated, ensure_ascii=False, indent=1), encoding="utf-8")
                    translation_source_file(p.work, target).write_text(json.dumps(texts, ensure_ascii=False, indent=1), encoding="utf-8")
                    log(i18n.t("log.translated", s=round(time.perf_counter() - t)))
                final = segmentation.split_too_long([Cue(s.start, s.end, t) for s, t in zip(p.cues, translated)], settings)
                final = segmentation.adjust_durations(final, settings)
                srt = p.work / f"{p.base}.{target}.srt"
                srt.write_text(formats.srt(final, settings), encoding="utf-8")
                (p.work / f"{p.base}.{target}.vtt").write_text(formats.vtt(final, settings), encoding="utf-8")
                files.append(OutputFile(target, srt, len(final), True))
                log(i18n.t("log.target_subtitles", n=len(final), lang=i18n.language_name(target), file=srt.name))
        finally:
            if translator is not None:
                translator.finish()

    # 5. Copies next to the video and/or into the output folder, named after the video + language
    step("files")
    dest = destination(r)
    folders = []
    if dest.get("next_to_video"):
        folders.append(p.video.parent)
    if dest.get("output_dir") and p.output_dir.resolve() not in {x.resolve() for x in folders}:
        folders.append(p.output_dir)
    if not folders:
        log(i18n.t("log.no_destination"))
    all_copies = []
    fingerprints_file = p.work / FINGERPRINTS_FILE
    try:
        fingerprints = json.loads(fingerprints_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        fingerprints = {}
    for f in files:
        for folder in folders:
            target = folder / f"{p.video.stem}.{f.language}.srt"
            if target.resolve() != f.path.resolve():
                place_srt(f.path, target, fingerprints, r.redo, log)
                f.copies.append(target)
        if f.copies:
            f.path = f.copies[0]
        all_copies.extend(f.copies)
    if fingerprints:
        fingerprints_file.write_text(json.dumps(fingerprints, ensure_ascii=False, indent=1), encoding="utf-8")
    for c in all_copies:
        log(i18n.t("log.subtitles_file", path=c))

    # 6. Cleanup of the working folder
    if r.cleanup:
        opts = cleanup.options(cfg)
        if not all_copies and opts.get("subtitles"):
            opts = {**opts, "subtitles": False}       # only copy of the results: keep it
        cleanup.clean_folder(p.work, opts, log)

    main = next((f for f in files if f.translated), files[0])
    duration = time.perf_counter() - p.start
    log((i18n.t("log.subtitles_ready") if duration < 5 else i18n.t("log.subtitles_done", s=round(duration))) + " " + i18n.t("log.video", path=p.video))
    return Result(p.video, main.path, p.source_srt, p.code, main.count, main.translated, all_copies, duration, files)


def dub_and_mux(r: Request, p: Prepared, res: Result, until: str, log=print, step=lambda name: None, cancelled=lambda: False, replace: bool = False,
                language=None) -> dict:
    """After the translation: a dubbed voice for each language chosen (`language`: a code, a list, or None for the
    first translated language) that was translated, then the multi-track MKV with them all, which can replace the
    video. A voice already made is kept; a language added later makes the MKV again (or, once the MKV replaced the
    video, is added to it). Returns the paths produced."""
    translated = {f.language: f for f in res.files if f.translated}
    wanted = steps.dub_targets(list(translated), res.language, language)
    voices = [c for c in wanted if c in translated]
    missing = [c for c in wanted if c not in translated]
    if missing:
        log(i18n.t("log.dub_not_translated", langs=", ".join(missing)))
    if not voices:
        log(i18n.t("log.dub_no_translation"))
        return {"not_dubbed": i18n.t("log.dub_no_translation")}
    integrated = steps.integration_mark(p.work).is_file()
    made = steps.muxed_languages(p.work)
    if made is None and (integrated or steps.mkv_path(res.video).exists()):
        made = voices[:1]                          # an MKV made before several languages: its one voice
    # a voice made before its translation changed (lines translated again after a fix): made again, the MKV with it
    stale = [] if integrated else [c for c in voices if _older(steps.dubbing_file(p.work, c), translation_file(p.work, c))]
    if stale:
        log(i18n.t("log.dub_stale", langs=", ".join(stale)))
    already = set(made or []) >= set(voices) and not r.redo and not stale
    with_mkv = steps.goes_to(until, "mux")
    if integrated and already:
        log(i18n.t("log.dub_integrated"))
        return {}
    mkv = steps.mkv_path(res.video)
    if with_mkv and mkv.exists() and already:
        log(i18n.t("log.mkv_already", file=mkv.name))
        return _integrate(p, res.video, mkv, len(voices) + len(res.files), log) if replace else {"mkv": str(mkv)}
    if cancelled():
        raise Cancelled()
    step("dubbing")
    to_dub = [c for c in voices if c not in (made or [])] if integrated else voices    # in the video already: kept
    audios = []
    on = getattr(r, "on_progress", None)           # the voices one after the other: one progress for them all
    for n, code in enumerate(to_dub):
        step("dubbing")                            # back from the mixing of the voice before
        voice = [n + 1, len(to_dub)] if len(to_dub) > 1 else None
        progress = (lambda d, total, n=n, voice=voice: on(n * total + d, len(to_dub) * total, 0, voice)) if on else None
        try:
            audio = dubbing.dub(r.cfg, res.video, p.work, res.files[0].path, translated[code].path, code, log, cancelled,
                                r.redo or code in stale, progress=progress, phase=step)
        except dubbing.DubbingError:
            if cancelled():
                raise Cancelled() from None
            raise
        audios.append((code, audio))
    dubbing.clear_temporary(p.work)                # every voice is made: the separated voice and background are no longer needed
    if not with_mkv:
        return {"dubbing": str(audios[0][1]), "dubbings": {c: str(a) for c, a in audios}}
    step("mux")
    subs = [(f.language, f.path) for f in res.files]
    if integrated:                                 # the video is the MKV: only the subtitles it does not hold yet
        present = {(s.get("tags") or {}).get("language") for s in muxing.probe(res.video)["streams"] if s.get("codec_type") == "subtitle"}
        subs = [(c, f) for c, f in subs if muxing.code3(c) not in present]
    mkv = muxing.mux(res.video, audios, audios[0][0], res.language, subs, log, r.redo or mkv.exists())
    mark = steps.mux_mark(p.work)
    mark.parent.mkdir(parents=True, exist_ok=True)
    mark.write_text(json.dumps({"languages": list(dict.fromkeys([*(made or [] if integrated else []), *(c for c, _ in audios)]))}),
                    encoding="utf-8")
    return {"dubbing": str(audios[0][1]), "dubbings": {c: str(a) for c, a in audios},
            **(_integrate(p, res.video, mkv, len(audios) + len(subs), log) if replace else {"mkv": str(mkv)})}


def _integrate(p: Prepared, video: Path, mkv: Path, added: int, log) -> dict:
    final = muxing.replace_original(video, mkv, added, log)
    if final == mkv:
        return {"mkv": str(mkv)}
    mark = steps.integration_mark(p.work)
    mark.parent.mkdir(parents=True, exist_ok=True)
    mark.write_text(str(final), encoding="utf-8")
    return {"mkv": str(final), "replaced": True}
