"""The speeds a project gives the page to estimate a run before it starts (summary "rates"): the subtitles per second of
video, with the translation added when it is done apart (after a pause), and the dubbing."""
import pytest

from subtitles.web import observed
from subtitles import config
from subtitles.project import Stage
from subtitles.web import timing
from subtitles.web.manager import Manager
from subtitles.web.tasks import TaskQueue


@pytest.fixture
def run(tmp_path, monkeypatch):
    cfg = {"output_dir": str(tmp_path / "out"), "youtube": {"command": ["yt-dlp"]}, "target_languages": ["fr", "es"]}
    monkeypatch.setattr(config, "load", lambda *_a, **_k: cfg)
    g = Manager(TaskQueue(history=[]), start=False)
    monkeypatch.setattr(observed, "measure_durations", lambda *_a: None)
    f = tmp_path / "one.mp4"
    f.write_bytes(b"x")
    return g.projects.create(tmp_path.as_posix(), [{"source": f.as_posix()}], {}, "Plan", [Stage.SUBTITLES], start=False)


def test_rates_with_the_translation_apart(run, monkeypatch):
    rates = {1: 0.1, 2: 2.0, 3: 0.3}
    monkeypatch.setattr(timing, "known_rate", lambda n, o: rates[n])
    run.options.update(pause=True, languages=["fr", "es"])
    r = run.plan_rates()
    assert r["subtitles"] == pytest.approx(0.1 + timing.LINES_PER_SECOND * 2.0 * 2, abs=1e-4) and r["dubbing"] == 0.3
    assert run.summary()["rates"] == r


def test_rates_without_a_pause_or_unknown(run, monkeypatch):
    monkeypatch.setattr(timing, "known_rate", lambda n, o: {1: 0.5, 2: 2.0, 3: None}[n])
    run.options.update(pause=False, languages=["fr"])
    assert run.plan_rates() == {"subtitles": 0.5, "dubbing": None}          # the translation is in the measure of pass 1
    monkeypatch.setattr(timing, "known_rate", lambda n, o: None)
    assert run.plan_rates() == {"subtitles": None, "dubbing": None}
