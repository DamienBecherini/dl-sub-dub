"""Step 4, Dubbing: the dubbed language (of the project, of a video), the MKV or the dubbed track alone, what the
page shows of the engine."""

from types import SimpleNamespace

import pytest

from helpers import at
from subtitles.web import observed
from subtitles import config, dubbing, muxing, pipeline, steps
from subtitles.project import Stage, StageState
from subtitles.web import downloads
from subtitles.web.manager import Manager
from subtitles.project.choices import stop_for
from subtitles.web.tasks import TaskQueue


def test_the_mkv_choice_sets_how_far_the_work_goes():
    assert stop_for([Stage.SUBTITLES, Stage.DUBBING], "mux", mkv=False) == "dubbing"
    assert stop_for([Stage.SUBTITLES, Stage.DUBBING], "dubbing", mkv=True) == "mux"
    assert stop_for([Stage.SUBTITLES, Stage.DUBBING], "translation", mkv=False) == "dubbing"     # ticked again later
    assert stop_for([Stage.SUBTITLES], "mux", mkv=False) == "translation"


def test_the_dubbed_language_is_one_translated():
    assert steps.dub_target(["fr", "en", "de"], "en") == "fr"
    assert steps.dub_target(["fr", "en", "de"], "en", "de") == "de"
    assert steps.dub_target(["fr", "de"], "en", "en") == "fr"            # the spoken one is not dubbed
    assert steps.dub_target(["en"], "en") is None
    assert steps.dub_targets(["fr", "en", "de"], "en") == ["fr", "de"]          # none chosen: all but the spoken one


def test_dub_and_mux_takes_the_language_chosen(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(dubbing, "dub", lambda cfg, video, work, src, target_srt, target, *a, **k: seen.append((target, target_srt)) or tmp_path / "d.m4a")
    files = [SimpleNamespace(language="en", path=tmp_path / "v.en.srt", translated=False),
             SimpleNamespace(language="fr", path=tmp_path / "v.fr.srt", translated=True),
             SimpleNamespace(language="de", path=tmp_path / "v.de.srt", translated=True)]
    r, p = SimpleNamespace(redo=False, cfg={}), SimpleNamespace(work=tmp_path / "work")
    res = SimpleNamespace(video=tmp_path / "v.mp4", language="en", files=files)
    pipeline.dub_and_mux(r, p, res, "dubbing", log=lambda *_: None, language="de")
    said = []
    assert pipeline.dub_and_mux(r, p, res, "dubbing", log=said.append, language="it")["not_dubbed"]   # not translated: said, not another one
    assert "it" in said[0]
    pipeline.dub_and_mux(r, p, res, "dubbing", log=lambda *_: None)
    nothing = SimpleNamespace(video=res.video, language="en", files=files[:1])
    assert pipeline.dub_and_mux(r, p, nothing, "dubbing", log=lambda *_: None)["not_dubbed"]
    assert [t for t, _ in seen] == ["de", "fr", "de"] and seen[0][1] == tmp_path / "v.de.srt"   # none chosen: every one translated
    seen.clear()
    out = pipeline.dub_and_mux(r, p, res, "dubbing", log=lambda *_: None, language=["de", "fr", "en"])   # several; the spoken one left out
    assert [t for t, _ in seen] == ["de", "fr"] and set(out["dubbings"]) == {"de", "fr"}


def test_an_mkv_with_a_voice_per_language(tmp_path, monkeypatch):
    voices = {c: tmp_path / f"{c}.m4a" for c in ("fr", "de")}
    monkeypatch.setattr(dubbing, "dub", lambda cfg, video, work, src, srt, target, *a, **k: voices[target])
    made = []
    monkeypatch.setattr(muxing, "mux", lambda video, dubbed, target, spoken, subs, log, redo: made.append((dubbed, redo)) or steps.mkv_path(video))
    files = [SimpleNamespace(language="en", path=tmp_path / "v.en.srt", translated=False),
             SimpleNamespace(language="fr", path=tmp_path / "v.fr.srt", translated=True),
             SimpleNamespace(language="de", path=tmp_path / "v.de.srt", translated=True)]
    r, p = SimpleNamespace(redo=False, cfg={}), SimpleNamespace(work=tmp_path / "work")
    res = SimpleNamespace(video=tmp_path / "v.mp4", language="en", files=files)
    pipeline.dub_and_mux(r, p, res, "mux", log=lambda *_: None, language="fr")
    assert made[-1] == ([("fr", voices["fr"])], False) and steps.muxed_languages(p.work) == ["fr"]
    steps.mkv_path(res.video).write_bytes(b"mkv")
    pipeline.dub_and_mux(r, p, res, "mux", log=lambda *_: None, language="fr")            # made: nothing again
    assert len(made) == 1
    pipeline.dub_and_mux(r, p, res, "mux", log=lambda *_: None, language=["fr", "de"])    # a language added: the MKV again
    assert made[-1] == ([("fr", voices["fr"]), ("de", voices["de"])], True) and steps.muxed_languages(p.work) == ["fr", "de"]
    work = tmp_path / "work"
    assert steps.observe(work, res.video, ["fr", "de"], "en", set(), ["fr", "de"])["muxed"]
    assert not steps.observe(work, res.video, ["fr", "de", "es"], "en", set(), ["fr", "de", "es"])["muxed"]


@pytest.fixture
def g(tmp_path, monkeypatch):
    cfg = {"output_dir": str(tmp_path / "out"), "youtube": {"command": ["yt-dlp"]}, "target_languages": ["fr", "de"],
           "dubbing": {"enabled": True, "command": ["python"], "model": "k2-fsa/OmniVoice", "steps": 32}}
    monkeypatch.setattr(config, "load", lambda *_a, **_k: cfg)
    monkeypatch.setattr(downloads.DownloadJob, "start", lambda self: None)
    g = Manager(TaskQueue(history=[]), start=False)
    monkeypatch.setattr(observed, "measure_durations", lambda *_a: None)
    return g


def test_settings_of_the_dubbing_step(g, tmp_path):
    a = tmp_path / "a.mp4"
    a.write_bytes(b"x")
    run = g.projects.create(tmp_path.as_posix(), [{"source": a.as_posix()}], {}, "Mine", [Stage.SUBTITLES, Stage.DUBBING], start=False)
    d = run.summary()["dubbing"]
    card = d.pop("gpu")
    assert d == {"available": True, "enabled": True, "model": "k2-fsa/OmniVoice", "steps": 32, "language": "", "languages": [], "mkv": True, "replace": False}
    assert card["setting"] == "auto" and card["sharing"] == "one_at_a_time" and not card["local"] and card["free"] == ""
    assert run.options["stop"] == "mux"
    assert g.projects.set_project_settings(run.id, {"mkv": False, "dub_language": "DE", "replace": True})
    assert run.options["stop"] == "dubbing" and run.summary()["dubbing"]["language"] == "de"
    assert g.projects.set_project_settings(run.id, {"mkv": True})
    assert run.options["stop"] == "mux" and run.summary()["dubbing"]["replace"] is True
    item = run.project.items[0]
    assert g.projects.set_item_settings(run.id, item.id, {"dub_language": "fr"}) and item.overrides["dub_language"] == ["fr"]
    assert g.projects._add_task(run, item).options["dub_language"] == ["fr"]
    assert g.projects.set_item_settings(run.id, item.id, {"dub_language": ["ES", "fr"]}) and item.overrides["dub_language"] == ["es", "fr"]
    assert g.projects._add_task(run, item).options["languages"] == ["fr", "de", "es"]    # the dubbed ones translated too
    assert g.projects.set_item_settings(run.id, item.id, {"dub_language": None}) and "dub_language" not in item.overrides
    assert g.projects._add_task(run, item).options["dub_language"] == ["de"]
    assert g.projects.set_stages(run.id, [Stage.SUBTITLES]) and run.options["stop"] == "translation"
    assert g.projects.set_stages(run.id, [Stage.SUBTITLES, Stage.DUBBING]) and run.options["stop"] == "mux"   # the MKV chosen



def test_final_file_and_replacing_chosen_by_one_video(g, tmp_path):
    a, b = tmp_path / "a.mp4", tmp_path / "b.mp4"
    a.write_bytes(b"x")
    b.write_bytes(b"x")
    run = g.projects.create(tmp_path.as_posix(), [{"source": a.as_posix()}, {"source": b.as_posix()}], {}, "Mine",
                           [Stage.SUBTITLES, Stage.DUBBING], start=False)
    one, other = run.project.items
    assert run.options["stop"] == "mux" and not run.options.get("replace")
    assert g.projects.set_item_settings(run.id, one.id, {"mkv": 0, "replace": 1}) and one.overrides == {"mkv": False, "replace": True}
    t = g.projects._add_task(run, one, until="mux")
    assert t.options["until"] == "dubbing" and t.options["replace"] is True and g.projects.replaces_original(t)
    t = g.projects._add_task(run, other, until="mux")              # the other one keeps the project's
    assert t.options["until"] == "mux" and t.options["replace"] is False and not g.projects.replaces_original(t)
    assert g.projects._add_task(run, one).options.get("until") is None    # a translation: no stop of the dubbing added
    assert g.projects.set_item_settings(run.id, one.id, {"mkv": None, "replace": None}) and one.overrides == {}
    assert g.projects._add_task(run, one, until="mux").options["until"] == "mux"


def test_a_setting_of_its_own_ticks_the_step_of_the_video(g, tmp_path):
    a = tmp_path / "a.mp4"
    a.write_bytes(b"x")
    run = g.projects.create(tmp_path.as_posix(), [{"source": a.as_posix()}], {}, "Mine", [Stage.SUBTITLES, Stage.DUBBING], start=False)
    item = run.project.items[0]
    item.tick(None, False)
    assert not item.selected
    assert g.projects.set_item_settings(run.id, item.id, {"mkv": None})        # back to the project's: nothing ticked
    assert not item.selected
    assert g.projects.set_item_settings(run.id, item.id, {"dub_language": "fr"})   # the dubbing, and the subtitles it needs
    assert item.selected and item.stages[Stage.DUBBING].selected and item.stages[Stage.SUBTITLES].selected
    item.tick(None, False)
    assert g.projects.set_item_settings(run.id, item.id, {"max_height": 720})    # no download for a file: nothing
    assert not item.selected
    item.stages[Stage.SUBTITLES].state = StageState.DONE
    assert g.projects.set_item_settings(run.id, item.id, {"translator": "other"})  # done, and still done: left as it is
    assert not item.stages[Stage.SUBTITLES].selected and item.state(Stage.SUBTITLES) == StageState.DONE
    assert g.projects.set_item_settings(run.id, item.id, {"languages": ["es"]})    # a language not translated: to do again
    assert item.state(Stage.SUBTITLES) == StageState.TODO and item.stages[Stage.SUBTITLES].selected


def test_a_step_ticked_with_the_steps_it_needs(g, tmp_path):
    a = tmp_path / "a.mp4"
    a.write_bytes(b"x")
    run = g.projects.create(tmp_path.as_posix(), [{"source": a.as_posix()}, {"source": "https://www.youtube.com/watch?v=abcdefghijk"}],
                           {}, "Mine", [Stage.DOWNLOAD, Stage.SUBTITLES, Stage.DUBBING], start=False)
    file, address = run.project.items
    ticked = lambda i: [s for s in (Stage.DOWNLOAD, Stage.SUBTITLES, Stage.DUBBING) if i.stages[s].selected]   # noqa: E731
    for i in (file, address):
        i.tick(None, False)
    assert g.projects.select(run.id, {address.id: True}, Stage.DUBBING)            # dubbing: its subtitles and download
    assert ticked(address) == [Stage.DOWNLOAD, Stage.SUBTITLES, Stage.DUBBING]
    assert g.projects.select(run.id, {address.id: False}, Stage.DOWNLOAD)          # no download: nothing after it
    assert ticked(address) == [] and not address.selected
    assert g.projects.select(run.id, {file.id: True}, Stage.DUBBING) and ticked(file) == [Stage.SUBTITLES, Stage.DUBBING]
    file.stages[Stage.SUBTITLES].state = StageState.DONE
    assert g.projects.select(run.id, {file.id: False}, Stage.SUBTITLES)            # subtitles made: the dubbing stays
    assert ticked(file) == [Stage.DUBBING]

def test_a_dubbing_not_set_up(g, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "load", lambda *_a, **_k: {"output_dir": str(tmp_path / "out"), "dubbing": {"enabled": False}})
    a = tmp_path / "a.mp4"
    a.write_bytes(b"x")
    run = g.projects.create(tmp_path.as_posix(), [{"source": a.as_posix()}], {}, "Mine", [Stage.SUBTITLES, Stage.DUBBING], start=False)
    d = run.summary()["dubbing"]
    assert d["available"] is False and d["enabled"] is False
    assert not g.projects.run_item(run.id, run.project.items[0].id, Stage.DUBBING)


def test_dubbing_never_starts_the_subtitles(g, tmp_path):
    a = tmp_path / "a.mp4"
    a.write_bytes(b"x")
    run = g.projects.create(tmp_path.as_posix(), [{"source": a.as_posix()}], {}, "Mine", [Stage.SUBTITLES, Stage.DUBBING], start=False)
    item = run.project.items[0]
    assert not g.projects.run_item(run.id, item.id, Stage.DUBBING)          # not subtitled: a translation may be paid
    assert g.projects.start(run.id, [Stage.DUBBING], only=True)
    g.projects.feed()
    assert item.state(Stage.SUBTITLES) == StageState.TODO and item.state(Stage.DUBBING) == StageState.TODO
    d = run.summary()
    assert d["blocked"] == [] and d["items"][0]["stages"]["dubbing"]["lacks"] == "subtitles"      # said in its cell
    at(item, Stage.SUBTITLES, StageState.DONE)
    item.facts["translated"] = False
    assert not g.projects.run_item(run.id, item.id, Stage.DUBBING)          # nothing translated to dub
    item.facts["translated"] = True
    assert g.projects.run_item(run.id, item.id, Stage.DUBBING) and item.state(Stage.DUBBING) == StageState.RUNNING


def test_what_the_disk_shows_done_is_done(g, tmp_path, monkeypatch):
    found = {"transcribed": True, "reviewed": False, "validated": False, "translated": True, "dubbed": True, "muxed": True}
    monkeypatch.setattr(steps, "observe", lambda *a, **k: dict(found))
    monkeypatch.setattr("subtitles.pipeline.transcription_kept", lambda *a: True)
    a = tmp_path / "a.mp4"
    a.write_bytes(b"x")
    run = g.projects.create(tmp_path.as_posix(), [{"source": a.as_posix()}], {}, "Mine", [Stage.SUBTITLES, Stage.DUBBING], start=False)
    item = run.project.items[0]
    assert item.state(Stage.SUBTITLES) == StageState.DONE and item.state(Stage.DUBBING) == StageState.DONE
    found["muxed"] = False                                     # dubbed, but the MKV is wanted: the dubbing is to do
    run2 = g.projects.create((tmp_path / "b").as_posix(), [{"source": a.as_posix()}], {}, "Other", [Stage.SUBTITLES, Stage.DUBBING], start=False)
    assert run2.project.items[0].state(Stage.DUBBING) == StageState.TODO
