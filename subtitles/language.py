"""Language of the transcription from its function words (the Parakeet Ultra model does not return it)."""
import re

FUNCTION_WORDS = {
    "fr": "le la les un une des et est que qui dans pour pas ce il elle nous vous sur avec mais plus très sont être au du",
    "en": "the and is are to of that in it you for with this on was have not but they we can will from be as",
    "de": "der die das und ist nicht ich sie es ein eine zu den mit auf für von wir auch dass im nach wie",
    "es": "el la los las y es que en un una por para con no se del al como más pero sus también muy",
    "it": "il la di che e è un una per non con sono gli le del della anche ma come più questo nel",
    "pt": "o a os as e é que de um uma para com não se do da em mais mas como também muito isso",
    "nl": "de het een en is van dat niet ik je op met voor zijn er maar ook aan bij wat dit",
}
_SETS = {k: set(v.split()) for k, v in FUNCTION_WORDS.items()}

# French names of the languages (used in the translation instructions, which are written in French); a missing code
# is designated by its code.
NAMES = {
    "fr": "français", "en": "anglais", "de": "allemand", "es": "espagnol", "it": "italien", "pt": "portugais",
    "nl": "néerlandais", "pl": "polonais", "ru": "russe", "uk": "ukrainien", "tr": "turc", "ar": "arabe", "ja": "japonais",
    "zh": "chinois", "ko": "coréen", "sv": "suédois", "da": "danois", "no": "norvégien", "fi": "finnois", "cs": "tchèque",
    "ro": "roumain", "hu": "hongrois", "el": "grec", "he": "hébreu", "hi": "hindi", "ca": "catalan", "eu": "basque",
    "br": "breton", "oc": "occitan", "eo": "espéranto",
}


def name(code: str) -> str:
    return NAMES.get(code, f"langue de code « {code} »")


CODE = re.compile(r"[a-z]{2,3}(-[a-z0-9]{2,8})*")


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


def ask(translator, texts: list[str], chars: int = 1500) -> str:
    """The language of the texts asked to the translator (a language model), when the function words did not find it
    (another language, a short or mixed text): its code, or "" if the answer is not one."""
    sample = " ".join(t for t in texts if t).strip()[:chars]
    if len(sample.split()) < 3:
        return ""
    answer = translator.complete(ASK, sample)
    m = re.fullmatch(r"\W*(?:code\W*)?([a-z]{2})\W*", (answer or "").strip().lower())    # "ja", "Ja.", "code: ja"; not a sentence
    return m.group(1) if m and m.group(1) != "xx" else ""


def detect(text: str) -> str:
    """Language code (fr, en…) or an empty string if nothing stands out."""
    words = re.findall(r"[^\W\d_]+", text.lower())
    if len(words) < 5:
        return ""
    scores = {k: sum(w in s for w in words) / len(words) for k, s in _SETS.items()}
    lang, score = max(scores.items(), key=lambda kv: kv[1])
    return lang if score >= 0.06 else ""
