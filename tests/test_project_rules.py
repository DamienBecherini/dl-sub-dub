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
    assert rules.status(ctx(p, started=True, launched=True), p) == "suspended"
    assert rules.status(ctx(p, state="running", started=True, launched=True), p) == "running"
    at(f, SUB, S.DONE)
    assert rules.status(ctx(p, started=True, launched=True), p) == "done"
    assert rules.status(ctx(p, state="done", started=True, launched=True), p) == "done"
    p.items = [f, a]                                       # a video added (or ticked again) to a finished project: ready again
    a.tick(SUB, True)
    assert rules.status(ctx(p, state="done", started=True, launched=True), p) == "ready"
    at(a, SUB, S.CANCELLED)                                # cancelled: it ended too
    assert rules.status(ctx(p, state="done", started=True, launched=True), p) == "done"


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
    c = ctx(p, started=True, launched=True)
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
    alone = rules.project_view(ctx(p, started=True), p)             # a video ran from its line, Run never pressed
    assert not alone["run"]["resume"] and alone["status"] == "ready"   # "Run", not "Resume"; ready, not paused
    assert r["steps"]["subtitles"] == {"ready": 1, "waiting": 1, "why": "download"}


def test_a_video_run_on_its_own_leaves_run_nothing_to_start(tmp_path):
    """Run from its line in a project that does not run: Run has nothing to take for a step already at work (its button
    waits), and the project says "running", not "done"."""
    p, f, a = project(tmp_path, stages=[SUB])
    p.items = [f]
    at(f, SUB, S.RUNNING)
    for state in ("done", "suspended"):
        r = rules.project_view(ctx(p, state=state, started=True, launched=True), p)
        assert r["run"] == {"steps": [SUB], "open": [], "resume": False} and r["working"]
    assert rules.status(ctx(p, state="done", started=True, launched=True), p) == "running"
    assert rules.status(ctx(p, state="suspended", started=True, launched=True), p) == "running"        # a project paused or never run, too
    assert rules.status(ctx(p, state="suspended", started=True, launched=True, finishing=frozenset({f.id})), p) == "suspended"   # paused after the videos at work: it ends
    assert rules.status(ctx(p, state="running", started=True, launched=True), p) == "running" and not rules.alone(ctx(p, state="running"), p)
    f.tick(DUB, True)                                                                  # something more ticked: Run takes it
    p.stages = [SUB, DUB]
    assert rules.project_view(ctx(p, state="done", started=True, launched=True), p)["run"]["open"] == [DUB]
    at(f, SUB, S.DONE)
    f.tick(DUB, False)
    assert rules.status(ctx(p, state="done", started=True, launched=True), p) == "done"


def test_a_video_that_waits_on_its_own_asks_for_the_go_ahead(tmp_path):
    """Its task stopped before a paid model, in a project that does not run: the go-ahead is asked above the list as
    for a project that stopped for it. While the project runs, the videos transcribed wait for the others."""
    p, f, a = project(tmp_path, stages=[SUB])
    p.items = [f]
    at(f, SUB, S.PAUSED)
    for state in ("done", "suspended"):
        c = ctx(p, state=state, started=True, launched=True)
        assert rules.asks(c, p) and rules.status(c, p) == "awaiting_translation"
        r = rules.project_view(c, p)
        assert r["go_ahead"] and r["run"]["open"] == [SUB] and not r["working"]
    assert rules.asks(ctx(p, state="awaiting_translation", started=True, launched=True), p)
    running = ctx(p, state="running", started=True, launched=True)
    assert not rules.asks(running, p) and rules.status(running, p) == "running"
    paused = ctx(p, state="suspended", started=True, launched=True, agreed=frozenset({f.id}))       # it had the go-ahead and waits for its turn
    assert not rules.asks(paused, p) and rules.status(paused, p) == "suspended" and not rules.project_view(paused, p)["go_ahead"]
    assert not rules.asks(ctx(p, state="cancelled", started=True, launched=True), p)
    f.selected = False                                                                 # unticked: asks for nothing
    assert not rules.asks(ctx(p, state="done", started=True, launched=True), p)


# --- what the mutation testing found untested (mutmut, 21/11/2026: 552 of 575 changes of this module caught) ---------

def test_the_language_of_the_site_keeps_its_first_part(tmp_path):
    p, f, a = project(tmp_path)
    for given, found in (("pt_BR", "pt"), ("fr-FR", "fr"), ("EN", "en"), ("", "")):
        a.facts["site"] = {"language": given}
        assert rules.site_language(a) == found


def test_a_step_applies_only_if_it_can_be_done_and_is_not_left_out(tmp_path):
    p, f, a = project(tmp_path)
    assert not rules.applies(f, DL) and rules.applies(a, DL)          # a file has nothing to download
    at(a, DL, S.SKIPPED)
    assert not rules.applies(a, DL)
    at(f, SUB, S.DONE)
    f.facts["translated"] = True
    f.overrides["dub_language"] = ["en"]
    assert rules.can_run(ctx(p), f, DUB)
    at(f, DUB, S.PAUSED)
    assert not rules.can_run(ctx(p), f, DUB)                           # only the subtitles wait for a go-ahead
    at(f, DUB, S.SKIPPED)                                              # left out: what it would lack is not said
    assert json.loads(json.dumps(rules.item_view(ctx(p, dubbing=False), p, f)))["stages"]["dubbing"]["lacks"] is None


def test_the_buttons_of_a_line_in_a_closed_project_and_without_a_video(tmp_path):
    p, f, a = project(tmp_path)
    at(f, SUB, S.RUNNING)
    assert rules.actions(ctx(p), f, SUB) == ["cancel"] and rules.actions(ctx(p, state="cancelled"), f, SUB) == []
    at(a, SUB, S.DONE)                                                 # done, but its video is gone: nothing to play
    assert rules.actions(ctx(p), a, SUB) == [] and rules.actions(ctx(p), f, SUB) == ["cancel"]
    at(f, SUB, S.DONE)
    assert rules.actions(ctx(p), f, SUB) == ["play"]


def test_a_downloaded_address_has_no_download_left_in_its_chains(tmp_path):
    p, f, a = project(tmp_path)
    assert [DL] == rules.chains(ctx(p), a)[0][:1]
    at(a, DL, S.DONE)
    a.video = f.video
    assert all(DL not in chain for chain in rules.chains(ctx(p), a)) and all(DL not in chain for chain in rules.chains(ctx(p), f))


def test_the_step_a_line_shows(tmp_path):
    p, f, a = project(tmp_path, stages=[SUB])
    for s in (DL, SUB, DUB):
        f.tick(s, False)
    assert rules.current(p, f) is None                                 # nothing ticked, nothing done
    at(f, DUB, S.DONE)                                                 # dubbed earlier, outside the steps of the project
    assert rules.current(p, f) == DUB
    assert rules.log_step(a) == SUB                                    # nothing ran: the subtitles
    at(a, DL, S.DONE)
    assert rules.log_step(a) == DL and json.loads(json.dumps(rules.item_view(ctx(p), p, a)))["log_step"] == "download"


def test_run_downloads_only_with_addresses_and_takes_no_step_the_project_does_not_run(tmp_path):
    p, f, a = project(tmp_path, stages=[DL, SUB])
    assert rules.run_steps(p) == [DL, SUB]
    a.tick(SUB, False)                                                 # only its download ticked: Run still downloads
    assert rules.run_steps(p) == [DL, SUB]
    a.tick(SUB, True)
    p.items = [f]
    assert rules.run_steps(p) == [SUB]                                 # only files: nothing to download
    p, f, a = project(tmp_path, stages=[SUB])
    p.items = [a]
    a.tick(SUB, False)
    a.tick(DL, True)
    a.tick(DUB, True)                                                  # its download and its dubbing ticked, not run by this project
    assert rules.run_steps(p) == [SUB]


def test_an_address_is_blocked_when_its_download_is_not_ticked_whatever_the_steps_before(tmp_path):
    p, f, a = project(tmp_path)
    a.tick(DL, False)
    assert rules.blocked(p, [DL, SUB]) == [a]                          # the download skipped: its subtitles have no video
    a.tick(DL, True)
    assert rules.blocked(p, [DL, SUB]) == []
    at(a, DL, S.DONE)                                                  # said done, and still no video
    assert rules.blocked(p, [DL, SUB]) == [a]


def test_resume_needs_a_step_that_can_start(tmp_path):
    p, f, a = project(tmp_path, stages=[DUB])
    p.items = [f]
    at(f, SUB, S.DONE)
    f.facts["translated"] = True
    f.overrides["dub_language"] = ["en"]
    f.tick(DUB, True)
    assert rules.project_view(ctx(p, started=True, launched=True), p)["run"]["resume"]
    assert not rules.project_view(ctx(p, started=True, launched=True, dubbing=False), p)["run"]["resume"]     # the dubbing not set up
