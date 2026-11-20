"""Language of the transcription from its function words (the Parakeet Ultra model does not return it), and whether a
transcription looks like its language at all (`doubt`).

The function words of a language (its small frequent words: articles, pronouns, conjunctions) are a list in a file,
function_words/<code>.txt: lines that start with # are comments, the rest is the words, separated by spaces or line
breaks. The lists that come with the tool are those of the 25 languages of Parakeet. The user adds a language or
replaces a list with a file of the same name and shape in the folder `function_words` of the settings folder
(USER_LISTS): a transcription engine that knows other languages needs their lists for its transcriptions to be
recognised and judged. `python -m subtitles.language <text> [code]` measures a text against the lists.
"""
import json
import re
import sys
import threading
from pathlib import Path

from . import config

CODE = re.compile(r"[a-z]{2,3}(-[a-z0-9]{2,8})*")
WORD = re.compile(r"[^\W\d_]+")                # a word of a text: letters of any alphabet (l'homme, il-ktieb: two words)

BUILT_IN_LISTS = Path(__file__).resolve().parent / "function_words"
USER_LISTS = config.user_dir() / "function_words"    # changed when the data moves (data_location.repoint)
_lists: tuple[tuple, dict[str, set[str]]] = ((), {})
_lists_lock = threading.Lock()


def _list_files() -> list[Path]:
    """The files of the lists, the user's after those of the tool (a later file replaces an earlier one of its name)."""
    files = []
    for folder in (BUILT_IN_LISTS, USER_LISTS):
        try:
            files += sorted(f for f in folder.glob("*.txt") if CODE.fullmatch(f.stem.lower()))
        except OSError:
            pass
    return files


def read_list(file: Path) -> set[str]:
    """The words of a list file, in lower case, cut as the words of a text are."""
    lines = file.read_text(encoding="utf-8-sig").splitlines()
    return {w for line in lines if not line.lstrip().startswith("#") for w in WORD.findall(line.lower())}


def word_sets() -> dict[str, set[str]]:
    """The function words of each language that has a list: {code: words}. Read again when a file was added, removed
    or changed; a file that cannot be read, or without a word, is left out (the list of the tool then stays)."""
    global _lists
    files = _list_files()
    stamp = []
    for f in files:
        try:
            s = f.stat()
            stamp.append((str(f), s.st_mtime_ns, s.st_size))
        except OSError:
            pass
    with _lists_lock:
        if _lists[0] != tuple(stamp):
            sets: dict[str, set[str]] = {}
            for f in files:
                try:
                    words = read_list(f)
                except (OSError, UnicodeDecodeError):
                    continue
                if words:
                    sets[f.stem.lower()] = words
            _lists = (tuple(stamp), sets)
        return _lists[1]


# French names of the languages (used in the translation instructions, which are written in French); a missing code
# is designated by its code.
NAMES = {
    "fr": "français", "en": "anglais", "de": "allemand", "es": "espagnol", "it": "italien", "pt": "portugais",
    "nl": "néerlandais", "pl": "polonais", "ru": "russe", "uk": "ukrainien", "tr": "turc", "ar": "arabe", "ja": "japonais",
    "zh": "chinois", "ko": "coréen", "sv": "suédois", "da": "danois", "no": "norvégien", "fi": "finnois", "cs": "tchèque",
    "ro": "roumain", "hu": "hongrois", "el": "grec", "he": "hébreu", "hi": "hindi", "ca": "catalan", "eu": "basque",
    "br": "breton", "oc": "occitan", "eo": "espéranto", "bg": "bulgare", "hr": "croate", "et": "estonien", "lv": "letton",
    "lt": "lituanien", "mt": "maltais", "sk": "slovaque", "sl": "slovène",
}


def name(code: str) -> str:
    return NAMES.get(code, f"langue de code « {code} »")


def normalize_codes(value) -> list[str]:
    """Clean list of language codes, without duplicates, from a list (lists inside it too) or a string "fr, en".
    Only what looks like a code is kept: a list turned into text by mistake ("['en']") is left out."""
    if isinstance(value, str):
        value = re.split(r"[,\s;]+", value)
    seen: list[str] = []
    for v in value or []:
        for c in (normalize_codes(v) if isinstance(v, (list, tuple)) else [str(v).strip().lower()]):
            if CODE.fullmatch(c) and c not in seen:
                seen.append(c)
    return seen


ASK = ("You identify the language of a text taken from the subtitles of a video. Answer with its ISO 639-1 code only "
       "(two lowercase letters, for example: fr, en, ja), nothing else.")


# The same question for a transcription that looks little like its language (see `doubt`): the engine may have written
# by ear a language it does not know, and the text then reads as no language at all.
ASK_SPOKEN = ("A speech recognition engine wrote this text from the sound of a video. It may not know the language spoken: "
              "it then writes what it hears with the words and the letters of another language, and the text makes little "
              "sense. Answer with the ISO 639-1 code of the language most probably spoken in the video (two lowercase "
              "letters, for example: fr, en, ar, ja), nothing else.")


def ask(translator, texts: list[str], chars: int = 1500, question: str = ASK) -> str:
    """The language of the texts asked to the translator (a language model), when the function words did not find it
    (another language, a short or mixed text): its code, or "" if the answer is not one."""
    sample = " ".join(t for t in texts if t).strip()[:chars]
    if len(sample.split()) < 3:
        return ""
    answer = translator.complete(question, sample)
    m = re.fullmatch(r"\W*(?:code\W*)?([a-z]{2})\W*", (answer or "").strip().lower())    # "ja", "Ja.", "code: ja"; not a sentence
    return m.group(1) if m and m.group(1) != "xx" else ""


def shares(text: str) -> tuple[int, dict[str, float]]:
    """The words of a text, and for each language that has a list the share of them that are its function words."""
    words = WORD.findall(text.lower())
    return len(words), {k: sum(w in s for w in words) / len(words) if words else 0.0 for k, s in word_sets().items()}


def detect(text: str) -> str:
    """Language code (fr, en…) or an empty string if nothing stands out."""
    count, scores = shares(text)
    if count < 5 or not scores:
        return ""
    lang, score = max(scores.items(), key=lambda kv: kv[1])
    return lang if score >= 0.06 else ""


# A transcription whose language the engine does not know is written by ear, with the letters of a language it knows:
# few of its words are function words of any language. Measured on 20/11/2026 over 286 transcriptions by Parakeet (264
# in English, 21 in French, 1 in German): the function words of their language are 16.7 % of the words at the lowest
# over a whole video, 15 % over any 200 words in a row; a video in Arabic, found "Italian", had 6.7 %. The lists of the
# other languages cover 20 to 38 % of the words of film subtitles in their language (see each file).
DOUBT = 0.12                                   # below this share, the transcription looks little like its language
DOUBT_MIN_WORDS = 200                          # fewer words: too short to tell
DOUBT_USUAL = 0.15                             # what a right transcription has at least, said with the warning


def doubt(text: str, code: str = "") -> dict | None:
    """Whether a transcription looks too little like its language to be trusted: {"code", "share", "words", "usual"}
    (`share`: the function words of that language among its words, 0 to 1; `usual`: what a right transcription has at
    least; `code` "" when no language stood out), or None when
    it looks right or cannot be told: a text too short, or a language without a list (chosen by the user, given by
    the engine or found by a model: nothing to compare it with; a list for it makes it judged, see the module)."""
    count, scores = shares(text)
    if count < DOUBT_MIN_WORDS or not scores:
        return None
    if code in scores:
        share = scores[code]
    elif code and code != "xx":
        return None
    else:
        code, share = "", max(scores.values())
    return {"code": code, "share": round(share, 3), "words": count, "usual": DOUBT_USUAL} if share < DOUBT else None


# --- python -m subtitles.language <text> [code]: a text measured against the lists (to write or improve one) ---------

def text_of(file: Path) -> str:
    """The text of a transcription kept by the tool (transcription.json), of subtitles (.srt, .vtt: their numbers and
    times are not words) or of any text file."""
    raw = file.read_text(encoding="utf-8-sig", errors="replace")
    if file.suffix.lower() == ".json":
        d = json.loads(raw)
        return str(d.get("text") or " ".join(str(w.get("word", "")) for w in d.get("words") or []))
    return "\n".join(line for line in raw.splitlines() if "-->" not in line)


def report(text: str, code: str = "") -> str:
    """What the lists say of a text: its words, the share of each language (best first), the language found, and
    whether it would be held as doubtful (for `code` when given: the language chosen or expected)."""
    count, scores = shares(text)
    lines = [f"{count} words; lists: {len(scores)} ({USER_LISTS}: {len([f for f in _list_files() if f.parent == USER_LISTS])} of yours)"]
    lines += [f"  {k:8} {v:6.1%}" for k, v in sorted(scores.items(), key=lambda kv: -kv[1])[:8]]
    found = detect(text)
    lines.append(f"language found: {found or 'none (a model is asked, or the subtitles are named xx)'}")
    if count < DOUBT_MIN_WORDS:
        lines.append(f"too short to be judged (fewer than {DOUBT_MIN_WORDS} words)")
    elif (code or found) and (code or found) not in scores:
        lines.append(f"not judged: no list for {code or found}")
    else:
        d = doubt(text, code or found or "xx")
        lines.append(f"doubtful: under {DOUBT:.0%} for {d['code'] or 'any language'} ({d['share']:.1%})" if d
                     else f"right: {DOUBT:.0%} or more for {code or found} (a right transcription has {DOUBT_USUAL:.0%} or more)")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not args or args[0] in ("-h", "--help"):
        print("python -m subtitles.language <transcription.json | subtitles.srt | text file> [language code]\n"
              "Measures a text against the lists of function words (one file a language): those of the tool in\n"
              f"{BUILT_IN_LISTS}, yours in {USER_LISTS} (same names: yours replace them).")
        return 0 if args else 2
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(report(text_of(Path(args[0])), (args[1] if len(args) > 1 else "").lower()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
