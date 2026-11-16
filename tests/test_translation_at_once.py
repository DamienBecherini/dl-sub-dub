"""Chunks of a video sent together (translator field chunks_at_once): the result in order, as one at a time; the first
chunk of each wave gets the lines translated before it, the others the original lines; an incomplete answer is done
again alone, halved; the requests really overlap."""
import re
import threading
import time

from subtitles import translation

LINE = re.compile(r"^(\d+)\. (.*)$")


class Fake:
    """Translates "x" into "X"; can drop the last line of the chunks that start with a given text."""

    def __init__(self, at_once=1, drop_on=None):
        self.at_once, self.drop_on = at_once, drop_on
        self.requests, self.running, self.most = [], 0, 0
        self.lock = threading.Lock()

    def prepare(self):
        pass

    def complete(self, system, request):
        with self.lock:
            self.requests.append(request)
            self.running += 1
            self.most = max(self.most, self.running)
        time.sleep(0.02)
        lines = request.split("Lignes à traduire :\n", 1)[1].splitlines()
        pairs = [m.groups() for m in map(LINE.match, lines) if m]          # the reminder after the lines is not one
        if self.drop_on and pairs[0][1] == self.drop_on and "Rappel" not in request:
            pairs = pairs[:-1]
        with self.lock:
            self.running -= 1
        return "\n".join(f"{n}. {t.upper()}" for n, t in pairs if not t.startswith("Rappel"))


TEXTS = [f"ligne {k}" for k in range(1, 58)]


def test_one_at_a_time_unchanged():
    f = Fake()
    assert translation.translate_texts(f, TEXTS, chunk_size=10, log=lambda _m: None) == [t.upper() for t in TEXTS]
    assert f.most == 1 and "Contexte déjà traduit" in f.requests[1] and "9. LIGNE 9" in f.requests[1]


def test_together_same_result_and_contexts():
    f, seen = Fake(at_once=3), []
    out = translation.translate_texts(f, TEXTS, chunk_size=10, log=lambda _m: None, progress=lambda d, n: seen.append(d))
    assert out == [t.upper() for t in TEXTS]
    assert f.most > 1                                                    # the requests overlap
    assert seen == [30, 57]                                              # one step per wave
    logs = []
    translation.translate_texts(Fake(at_once=3), TEXTS, chunk_size=10, log=logs.append)
    assert any("1" in m and "30" in m and "3" in m for m in logs[:1])      # the wave said when it leaves
    by_first = {r.split("Lignes à traduire :\n1. ")[1].splitlines()[0]: r for r in f.requests}
    assert "Contexte déjà traduit" in by_first["ligne 31"] and "30. LIGNE 30" in by_first["ligne 31"]   # first of its wave
    assert "langue d'origine" in by_first["ligne 21"] and "20. ligne 20" in by_first["ligne 21"]     # translated at the same time


def test_together_incomplete_chunk_done_again_alone():
    f, logs = Fake(at_once=3, drop_on="ligne 11"), []
    out = translation.translate_texts(f, TEXTS, chunk_size=10, log=logs.append)
    assert out == [t.upper() for t in TEXTS]
    assert any("5" in m for m in logs)                                   # redone in chunks of 5


def test_field_read():
    assert translation.chunks_at_once({}) == 1
    assert translation.chunks_at_once({"chunks_at_once": "3"}) == 3
    assert translation.chunks_at_once({"chunks_at_once": 99}) == translation.MAX_CHUNKS_AT_ONCE
    assert translation.chunks_at_once({"chunks_at_once": "x"}) == 1
    assert translation.create({"type": "openai", "url": "http://127.0.0.1:9", "chunks_at_once": 2}).at_once == 2
