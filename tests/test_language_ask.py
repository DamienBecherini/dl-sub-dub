"""Spoken language not found by its function words: asked to the translator. Only a bare code is taken; the subtitles of
the spoken language are renamed with it and it is kept in the working folder for the next pass."""
import json
from pathlib import Path

from subtitles import language, pipeline, translation
from subtitles.segmentation import Cue


class Fake:
    def __init__(self, answer):
        self.answer, self.asked = answer, []

    def complete(self, system, request):
        self.asked.append((system, request))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


TEXTS = ["こんにちは、今日は", "いい天気ですね", "散歩に行きましょう", "はい"]


def test_only_a_bare_code_is_taken():
    assert language.ask(Fake("ja"), ["ciao a tutti", "oggi parliamo di cucina"]) == "ja"
    assert language.ask(Fake(" Ja. "), TEXTS + ["a b c"]) == "ja"
    assert language.ask(Fake("code: ko"), ["a b c d"]) == "ko"
    assert language.ask(Fake("The language is Japanese, so it is ja"), ["a b c d"]) == ""
    assert language.ask(Fake("xx"), ["a b c d"]) == ""
    f = Fake("ja")
    assert language.ask(f, ["un mot"]) == "" and f.asked == []           # too short to ask


def prepared(tmp_path: Path) -> pipeline.Prepared:
    work = tmp_path / "work"
    work.mkdir()
    srt = work / "video.xx.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nhello\n", encoding="utf-8")
    cues = [Cue(0, 1, "a b"), Cue(1, 2, "c d e")]
    return pipeline.Prepared(tmp_path / "video.mp4", work, "video", "xx", cues, srt, tmp_path, 0.0)


def test_recognised_renames_and_keeps_it(tmp_path):
    lines = []
    p = pipeline._recognised(prepared(tmp_path), Fake("ja"), "IA locale", lines.append)
    assert p.code == "ja" and p.source_srt.name == "video.ja.srt" and p.source_srt.exists()
    assert not (p.work / "video.xx.srt").exists()
    assert json.loads((p.work / pipeline.ASKED_LANGUAGE).read_text(encoding="utf-8")) == {"code": "ja"}
    assert pipeline._asked_language(p.work) == "ja" and "ja" in lines[-1]


def test_not_recognised_leaves_it(tmp_path):
    lines = []
    p0 = prepared(tmp_path)
    p = pipeline._recognised(p0, Fake(translation.TranslationError("down")), "IA", lines.append)
    assert p is p0 and (p.work / "video.xx.srt").exists() and pipeline._asked_language(p.work) == ""
    assert "down" in lines[-1]
