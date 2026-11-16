"""Steps of a video: Transcription → AI review → Validation → Translation → Dubbing → MKV (mux).

What is done is read from the disk, to survive a restart of the service: a finished step is never redone.
`STOPS`: how far a project goes by itself.
"""
import json
from pathlib import Path

from . import language, review

ORDER = ("transcription", "review", "validation", "translation", "dubbing", "mux")
STOPS = ("validation", "translation", "dubbing", "mux")
MKV_SUFFIX = ".multi.mkv"


def goes_to(stop: str | None, step: str) -> bool:
    """Is the step part of the requested work? Without a stop, the chain ends at translation."""
    return ORDER.index(step) <= ORDER.index(stop or "translation")


def mkv_path(video: Path) -> Path:
    return Path(video).with_name(Path(video).stem + MKV_SUFFIX)


def dubbing_file(work: Path, target: str) -> Path:
    """In a subfolder: cleaning the working folder deletes the top-level .m4a files (extracted audio)."""
    return Path(work) / "dubbing" / f"{target}.m4a"


def mux_mark(work: Path) -> Path:
    """The dubbed languages put in the MKV ({"languages": [...]}): one added later makes the MKV again."""
    return Path(work) / "dubbing" / "mux.json"


def muxed_languages(work: Path) -> list[str] | None:
    """The dubbed languages of the MKV made, None if not written (an MKV made before several languages: one)."""
    try:
        return list(json.loads(mux_mark(work).read_text(encoding="utf-8")).get("languages") or [])
    except (OSError, ValueError, AttributeError):
        return None


def integration_mark(work: Path) -> Path:
    """Present when the MKV replaced the original video: no .multi.mkv next to it any more, but the dubbing is done."""
    return Path(work) / "dubbing" / "integrated.txt"


def _translation_kept(work: Path, video: Path, target: str) -> bool:
    return (work / f"translation.{target}.json").is_file() or video.with_name(f"{video.stem}.{target}.srt").is_file()


def dub_codes(chosen) -> list[str]:
    """The dubbed languages chosen: "" or None (the default), one code, or a list of codes."""
    if not chosen:
        return []
    return language.normalize_codes(chosen)        # only real codes (a value spoiled by an older service is left out)


def dub_targets(languages: list[str], spoken: str, chosen=None) -> list[str]:
    """Languages of the dubbed voices: those chosen (other than the spoken one; they are translated first), otherwise
    every language of the subtitles but the spoken one."""
    wanted = [c for c in dict.fromkeys(dub_codes(chosen)) if c != spoken]
    if wanted:
        return wanted
    return [c for c in dict.fromkeys(languages) if c != spoken]


def dub_target(languages: list[str], spoken: str, chosen=None) -> str | None:
    """The first dubbed language (see dub_targets)."""
    t = dub_targets(languages, spoken, chosen)
    return t[0] if t else None


def observe(work: Path, video: str | Path, languages: list[str], spoken: str, pending: set[str], dub=None) -> dict:
    """Steps done for a video. `pending`: names still to validate in the project (see review.pending_names); `dub`: the
    dubbed languages chosen (by default the first one translated); `languages` holds them (they are translated)."""
    work, video = Path(work), Path(video)
    targets = [c for c in languages if c != spoken]
    reviewed_file = work / review.VIDEO_FILE
    reviewed, validated = False, False
    try:
        names = json.loads(reviewed_file.read_text(encoding="utf-8")).get("names", [])
        reviewed, validated = True, not pending.intersection(names)
    except (OSError, ValueError, AttributeError):
        pass
    voices = dub_targets(languages, spoken, dub)
    made = muxed_languages(work)
    muxed = (mkv_path(video).is_file() or integration_mark(work).is_file()) and bool(voices) and (
        set(voices) <= set(made) if made is not None else len(voices) == 1)       # an MKV of before: one voice
    return {"transcribed": (work / "transcription.json").is_file(), "reviewed": reviewed, "validated": validated,
            "translated": bool(targets) and all(_translation_kept(work, video, c) for c in targets),
            "dubbed": muxed or (bool(voices) and all(dubbing_file(work, c).is_file() for c in voices)), "muxed": muxed}
