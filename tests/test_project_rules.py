"""What can be done now (project/rules.py): what a step lacks, the buttons of a line, what Run takes, the steps Run
runs. The service decides with these rules and sends their result to the page, which works none of it out."""
import json
from pathlib import Path

from helpers import at
from subtitles.project import Item, Project, Stage, StageState, rules, settings
from subtitles.project.rules import Context, Lack

S = StageState
CFG = {"language": "fr", "target_languages": ["fr"], "output_dir": "", "youtube": {"max_height": 1080}}     # "fr": a default never counted
DL, SUB, DUB = Stage.DOWNLOAD, Stage.SUBTITLES, Stage.DUBBING


def ctx(p: Project, **more) -> Context:
    return Context(lambda item: settings.resolve(CFG, p, item), **{"dubbing": True, "language": p.settings.get("language") or "", **more})


def project(tmp_path: Path, stages=(DL, SUB, DUB), **options) -> tuple[Project, Item, Item]:
    """A file and an address, every step ticked."""
    video = tmp_path / "a.mp4"
    video.write_bytes(b"video")
    f, a = Item(video.as_posix()), Item("https://x.example/a")
    return Project(str(tmp_path), [f, a], dict(options), list(stages)), f, a


def test_what_each_step_lacks(tmp_path):
    p, f, a = project(tmp_path)
    c = ctx(p)
    assert rules.lacks(c, a, DL) is None and rules.lacks(c, f, SUB) is None
    assert rules.lacks(c, a, SUB) == Lack.DOWNLOAD and rules.lacks(c, a, DUB) == Lack.SUBTITLES    # its subtitles wait for the video
    assert rules.lacks(c, f, DUB) == Lack.SUBTITLES
    at(f, SUB, S.DONE)
    f.facts["translated"] = False
    assert rules.lacks(c, f, DUB) == Lack.TRANSLATION
    f.facts["translated"] = True
    assert rules.lacks(c, f, DUB) is None
    f.facts["detected_language"] = "fr"                                   # French video, French asked: no other language to speak
    assert rules.lacks(c, f, DUB) == Lack.NOTHING and rules.dub_targets(c, f) == []
    f.overrides["dub_language"] = ["en"]
    assert rules.lacks(c, f, DUB) is None and rules.dub_targets(c, f) == ["en"]
    assert rules.lacks(ctx(p, dubbing=False), f, DUB) == Lack.SETUP


def test_the_spoken_language_is_the_one_found_then_chosen_then_of_the_site(tmp_path):
    p, f, a = project(tmp_path, language="de")
    c = ctx(p)
    assert rules.spoken(c, a) == "de"
    a.facts["site"] = {"language": "fr-FR"}
    assert rules.spoken(c, a) == "fr"
    a.overrides["language"] = "it"
    assert rules.spoken(c, a) == "it"
    a.facts["detected_language"] = "en"
    assert rules.spoken(c, a) == "en"


def test_the_buttons_of_a_line_follow_its_state(tmp_path):
    p, f, a = project(tmp_path)
    c = ctx(p)
    assert rules.actions(c, f, SUB) == ["run"] and rules.actions(c, a, DL) == ["run"]
    assert rules.actions(c, a, SUB) == [] and rules.actions(c, f, DUB) == []          # they lack something: said instead
    assert rules.actions(c, f, DL) == [] and not rules.applies(f, DL)                  # a file is not downloaded
    f.facts["transcribed"] = True
    assert rules.actions(c, f, SUB) == ["translate_left"]
    at(f, SUB, S.RUNNING)
    assert rules.actions(c, f, SUB) == ["cancel"] and not rules.can_run(c, f, SUB)
    at(f, SUB, S.PAUSED)
    assert rules.actions(c, f, SUB) == ["translate", "cancel"] and rules.can_run(c, f, SUB)
    at(f, SUB, S.ERROR, "no server")
    assert rules.actions(c, f, SUB) == ["retry"]
    at(f, SUB, S.CANCELLED)
    assert rules.actions(c, f, SUB) == ["retry"]
    at(f, SUB, S.DONE)
    assert rules.actions(c, f, SUB) == ["play"] and rules.can_run(c, f, SUB)           # done: opened, or made again from the menu
    at(f, DUB, S.ERROR, "CUDA out of memory")
    assert rules.actions(c, f, DUB) == ["one_at_a_time", "retry"]
    assert rules.actions(ctx(p, sharing="one_at_a_time"), f, DUB) == ["retry"]
    closed = ctx(p, state="cancelled")
    assert rules.actions(closed, f, DUB) == [] and rules.actions(closed, f, SUB) == ["play"] and not rules.can_run(closed, f, SUB)


def test_run_takes_what_is_ticked_left_and_lacks_nothing(tmp_path):
    p, f, a = project(tmp_path)
    c = ctx(p)
    assert rules.ready(c, f, SUB) and rules.ready(c, a, DL)
    assert rules.left(a, SUB) and not rules.ready(c, a, SUB)                           # ticked and to do, but not downloaded
    assert not rules.left(f, DL)
    f.tick(SUB, False)
    assert not rules.left(f, SUB) and not rules.ready(c, f, SUB)
    f.tick(SUB, True)
    at(f, SUB, S.ERROR, "x")
    assert rules.ready(c, f, SUB)                                                      # an error is taken again
    at(f, SUB, S.DONE)
    assert not rules.left(f, SUB)
    assert rules.step_counts(c, p, SUB) == {"ready": 0, "waiting": 1, "why": Lack.DOWNLOAD}
    f.facts["translated"] = True
    assert rules.step_counts(c, p, DUB) == {"ready": 1, "waiting": 1, "why": Lack.SUBTITLES}
    assert rules.step_counts(ctx(p, dubbing=False), p, DUB) == {"ready": 0, "waiting": 2, "why": Lack.SETUP}


def test_run_runs_the_steps_ticked_and_those_they_need(tmp_path):
    p, f, a = project(tmp_path, stages=[DUB])
    assert rules.run_steps(p) == [DL, SUB, DUB]                                        # to dub: subtitled, so downloaded
    at(a, DL, S.DONE)
    assert rules.run_steps(p) == [SUB, DUB]
    for i in (f, a):
        at(i, SUB, S.DONE)
    assert rules.run_steps(p) == [DUB]
    p.items = [f]
    p.stages = [DL, SUB]
    assert rules.run_steps(p) == [SUB]                                                 # no address: nothing to download
    f.selected = False
    assert rules.run_steps(Project(str(tmp_path), [f], {}, [DUB])) == [DUB]            # unticked: asks for nothing


def test_an_address_without_download_is_blocked(tmp_path):
    p, f, a = project(tmp_path, stages=[SUB])
    assert rules.blocked(p) == [a]
    assert rules.blocked(p, [DL, SUB]) == []                                           # its download runs: it only waits
    a.tick(DUB, True)
    assert rules.blocked(p, [DUB]) == [a]                                              # the file not subtitled: said in its cell
    a.selected = False
    assert rules.blocked(p) == []


def test_the_state_of_a_project_in_words(tmp_path):
    p, f, a = project(tmp_path, stages=[SUB])
    p.items = [f]
    assert rules.status(ctx(p), p) == "ready"
    assert rules.status(ctx(p, started=True), p) == "suspended"
    assert rules.status(ctx(p, state="running", started=True), p) == "running"
    at(f, SUB, S.DONE)
    assert rules.status(ctx(p, started=True), p) == "done"


def test_the_chains_and_the_current_step_of_a_line(tmp_path):
    p, f, a = project(tmp_path)
    c = ctx(p)
    assert rules.chains(c, a) == [[DL, SUB], [DL, SUB, DUB]] and rules.chains(c, f) == [[SUB, DUB]]
    assert rules.chains(ctx(p, dubbing=False), f) == []
    assert rules.current(p, a) == DL and rules.current(p, f) == SUB and rules.log_step(f) == SUB
    at(f, SUB, S.DONE)
    assert rules.chains(c, f) == [] and rules.current(p, f) == DUB
    at(f, DUB, S.RUNNING)
    assert rules.log_step(f) == DUB and rules.busy(f) and rules.locked([f]) and not rules.locked([a])
    at(f, DUB, S.DONE)
    assert rules.current(p, f) == DUB                                                  # all done: the last one, to open it


def test_what_the_page_is_sent(tmp_path):
    p, f, a = project(tmp_path)
    c = ctx(p, started=True)
    v = json.loads(json.dumps(rules.item_view(c, p, a)))
    assert v["stages"]["subtitles"] == {**a.stages[SUB].to_dict(), "applies": True, "lacks": "download", "blocking": False, "left": True,
                                        "ready": False, "actions": [], "runnable": False}
    assert v["stages"]["download"]["actions"] == ["run"] and v["stages"]["download"]["runnable"]
    assert v["current"] == "download" and v["chains"] == [["download", "subtitles"], ["download", "subtitles", "dubbing"]]
    assert v["dub_targets"] == ["fr"] and v["spoken"] == "" and not v["busy"] and not v["locked"]
    assert json.loads(json.dumps(rules.item_view(ctx(p, dubbing=False), p, f)))["stages"]["dubbing"]["blocking"]
    r = json.loads(json.dumps(rules.project_view(c, p)))
    assert r["run"] == {"steps": ["download", "subtitles", "dubbing"], "open": ["download", "subtitles", "dubbing"], "resume": True}
    assert r["status"] == "suspended" and r["finished"] == 0 and not r["working"] and not r["locked"] and not r["go_ahead"]
    assert r["steps"]["subtitles"] == {"ready": 1, "waiting": 1, "why": "download"}


def test_a_video_run_on_its_own_leaves_run_nothing_to_start(tmp_path):
    """Run from its line in a project that does not run: Run has nothing to take for a step already at work (its button
    waits), and the project says "running", not "done"."""
    p, f, a = project(tmp_path, stages=[SUB])
    p.items = [f]
    at(f, SUB, S.RUNNING)
    for state in ("done", "suspended"):
        r = rules.project_view(ctx(p, state=state, started=True), p)
        assert r["run"] == {"steps": [SUB], "open": [], "resume": False} and r["working"]
    assert rules.status(ctx(p, state="done", started=True), p) == "running"
    assert rules.status(ctx(p, state="suspended", started=True), p) == "running"        # a project paused or never run, too
    assert rules.status(ctx(p, state="suspended", started=True, finishing=frozenset({f.id})), p) == "suspended"   # paused after the videos at work: it ends
    assert rules.status(ctx(p, state="running", started=True), p) == "running" and not rules.alone(ctx(p, state="running"), p)
    f.tick(DUB, True)                                                                  # something more ticked: Run takes it
    p.stages = [SUB, DUB]
    assert rules.project_view(ctx(p, state="done", started=True), p)["run"]["open"] == [DUB]
    at(f, SUB, S.DONE)
    f.tick(DUB, False)
    assert rules.status(ctx(p, state="done", started=True), p) == "done"


def test_a_video_that_waits_on_its_own_asks_for_the_go_ahead(tmp_path):
    """Its task stopped before a paid model, in a project that does not run: the go-ahead is asked above the list as
    for a project that stopped for it. While the project runs, the videos transcribed wait for the others."""
    p, f, a = project(tmp_path, stages=[SUB])
    p.items = [f]
    at(f, SUB, S.PAUSED)
    for state in ("done", "suspended"):
        c = ctx(p, state=state, started=True)
        assert rules.asks(c, p) and rules.status(c, p) == "awaiting_translation"
        r = rules.project_view(c, p)
        assert r["go_ahead"] and r["run"]["open"] == [SUB] and not r["working"]
    assert rules.asks(ctx(p, state="awaiting_translation", started=True), p)
    running = ctx(p, state="running", started=True)
    assert not rules.asks(running, p) and rules.status(running, p) == "running"
    paused = ctx(p, state="suspended", started=True, agreed=frozenset({f.id}))       # it had the go-ahead and waits for its turn
    assert not rules.asks(paused, p) and rules.status(paused, p) == "suspended" and not rules.project_view(paused, p)["go_ahead"]
    assert not rules.asks(ctx(p, state="cancelled", started=True), p)
    f.selected = False                                                                 # unticked: asks for nothing
    assert not rules.asks(ctx(p, state="done", started=True), p)
