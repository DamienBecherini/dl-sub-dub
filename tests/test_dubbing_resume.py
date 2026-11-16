"""A dubbing stopped takes its sentences again: each one is written as it is made, in a folder of its language that a
stop keeps, that the voice once made (or a dubbing made again) removes."""
import json
import sys
from pathlib import Path

from subtitles import cleanup, dubbing, dubbing_worker, steps


def cfg() -> dict:
    return {"dubbing": {"enabled": True, "command": [sys.executable], "model": "m"}}


def fake_worker(tmp_path: Path, monkeypatch) -> None:
    """A dubbing program that writes its job in place of the voice."""
    p = tmp_path / "fake.py"
    p.write_text("import json, sys\nt = json.loads(sys.stdin.readline())\nopen(t['output'], 'w', encoding='utf-8').write(json.dumps(t))\n", encoding="utf-8")
    monkeypatch.setattr(dubbing, "WORKER", p)


def job(work: Path, code: str, **more) -> dict:
    return json.loads(dubbing.dub(cfg(), Path("v.mp4"), work, Path("a.srt"), Path("b.srt"), code, log=lambda _m: None, **more).read_text(encoding="utf-8"))


def test_a_sentence_is_named_after_what_it_is_made_from():
    key = lambda **k: dubbing_worker.sentence_key(**{"text": "Bonjour.", "start": 1.0, "end": 2.5, "language": "fr", "model": "m", "steps": 32,   # noqa: E731
                                                     "reference_ids": [3, 4], "reference_text": "Hello.", **k})
    assert key() == key() and len(key()) == 20
    changed = [key(text="Bonsoir."), key(start=1.2), key(end=3.0), key(language="de"), key(model="other"), key(steps=16),
               key(reference_ids=[3]), key(reference_text="Hello there.")]
    assert key() not in changed and len(set(changed)) == len(changed)          # anything else: another sentence, made again


def test_each_voice_has_its_own_sentences_and_a_voice_made_again_starts_from_nothing(tmp_path, monkeypatch):
    fake_worker(tmp_path, monkeypatch)
    de, en = job(tmp_path, "de"), job(tmp_path, "en")
    folder = steps.dubbing_file(tmp_path, "de").parent
    assert Path(de["sentences_dir"]) == folder / "sentences-de" and Path(en["sentences_dir"]) == folder / "sentences-en"
    kept = folder / "sentences-de" / "abc.wav"
    kept.parent.mkdir()
    kept.write_bytes(b"x")
    assert job(tmp_path, "de") == de and kept.exists()                         # already dubbed: nothing is run, nothing removed
    job(tmp_path, "de", redo=True)
    assert not kept.exists()                                                   # made again: the sentences of the voice before go


def test_the_sentences_taken_again_count_in_the_progress():
    seen = []
    said = dubbing.worker_message('{"key": "log.dub_resumed", "done": 300, "n": 682}', lambda d, n: seen.append((d, n)))
    assert seen == [(300, 682)] and "300" in said and "682" in said


def test_a_stop_keeps_the_sentences_and_the_end_removes_them(tmp_path):
    work = tmp_path / ".work" / "video"
    for name in ("dubbing/de.m4a", "dubbing/temp-es/refs/sentence.wav", "dubbing/sentences-es/a.wav", "dubbing/sentences-es/b.wav",
                 "dubbing/separation/vocals.wav"):
        (work / name).parent.mkdir(parents=True, exist_ok=True)
        (work / name).write_bytes(b"x" * 10)
    left = lambda: sorted(p.relative_to(work).as_posix() for p in work.rglob("*") if p.is_file())  # noqa: E731
    assert cleanup.clean_stopped_dubbing(work) == (1, 10)                      # only what a resume does not take again
    assert left() == ["dubbing/de.m4a", "dubbing/sentences-es/a.wav", "dubbing/sentences-es/b.wav", "dubbing/separation/vocals.wav"]
    assert cleanup.summary(tmp_path / ".work")["audio"] == {"files": 3, "bytes": 30}       # "Clean now" would take them
    assert dubbing.clear_temporary(work) == 2                                  # every voice asked is made
    assert left() == ["dubbing/de.m4a"]
