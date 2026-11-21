"""The longer paths of the page, in a real browser, on the real service (fixture `studio`, see page_site.py): the AI
review decided on the page, one go-ahead for several videos before a paid model, the dubbing up to the MKV, an address
downloaded then subtitled, one video taken through its steps from its menu, a project found again after the service
restarted, the settings changed."""
import json
import re

from page_site import ADDRESS, opened, until
from subtitles import config, steps
from subtitles.project import Stage, StageState

S = StageState
FIX = {"index": 0, "status": "to_fix", "spelling": "Gariaev", "french": "Gariaev", "confidence": "high", "reason": "son proche"}


def state_is(page, key: str):
    page.locator(f".nav-bar .chip.state-{key}").wait_for()


def states(run, stage=Stage.SUBTITLES) -> list[str]:
    return [str(i.state(stage)) for i in run.project.items]


def test_the_names_of_the_ai_review_are_decided_on_the_page_then_used(page, studio):
    video = studio.video()
    run = studio.project(video, review="names", review_engine="local")
    studio.review = [[FIX]]                                            # the model proposes "Gariaev" for "Gary Iev"
    opened(page, studio, run).locator(".run-main").click()
    names = page.locator(".names-panel")
    names.wait_for()
    assert "Gary Iev" in names.inner_text() and studio.asked == [("review", "local")]   # reviewed, nothing translated yet
    assert states(run) == ["paused"] and not studio.srt(video, "fr")
    names.get_by_role("button", name="Valider et continuer").click()
    state_is(page, "done")                                             # decided, and the translator is free: it goes on, no second click
    assert not page.locator(".translation-panel").count()
    assert "Gariaev" in studio.srt(video, "en") and "Gary Iev" not in studio.srt(video, "en")       # fixed in the spoken language
    assert "Gariaev" in studio.srt(video, "fr") and ("translation", "local") in studio.asked
    glossary = json.loads((studio.folder / "subtitles.glossary.json").read_text(encoding="utf-8"))
    assert "Gariaev" in json.dumps(glossary, ensure_ascii=False)       # kept for the other videos of the project


def test_the_sure_fixes_are_applied_without_stopping_then_one_is_taken_back(page, studio):
    video = studio.video()
    run = studio.project(video, review="names", review_engine="local")
    studio.review = [[FIX]]                                            # the model says "Gariaev" for "Gary Iev", and says it sure
    opened(page, studio, run)
    page.get_by_role("checkbox", name=re.compile("Appliquer sans me demander les corrections sûres")).click()
    until(page, lambda: run.options.get("review_auto") is True, "the setting of the project")
    page.locator(".run-main").click()
    state_is(page, "done")                                             # to its end: it never stopped for the name
    assert "Gariaev" in studio.srt(video, "en") and "Gariaev" in studio.srt(video, "fr") and not page.locator(".names-panel").count()
    applied = page.locator(".applied-panel")                           # what was applied is listed, folded
    applied.locator("summary").click()
    said = applied.inner_text()
    assert "1 correction appliquée sans vous demander" in said and "Gary Iev" in said and "Gariaev" in said and "son proche" in said
    applied.get_by_role("button", name=re.compile("Annuler cette correction")).click()
    applied.wait_for(state="detached")                                 # taken back: nothing applied any more
    assert json.loads((studio.folder / "subtitles.glossary.json").read_text(encoding="utf-8")) == []
    page.get_by_role("button", name="Toutes les actions pour cette vidéo").click()
    page.get_by_role("menuitem", name=re.compile("^Sous-titrer à nouveau")).or_(page.get_by_text(re.compile("^Sous-titrer à nouveau"))).first.click()
    until(page, lambda: "Gary Iev" in studio.srt(video, "fr"), "the subtitles made again without the fix", seconds=20)
    assert "Gariaev" not in studio.srt(video, "en") and "Gariaev" not in studio.srt(video, "fr")
    assert studio.asked.count(("review", "local")) == 1                # the name is not submitted again


def test_what_is_less_sure_stays_to_decide_under_the_go_ahead(page, studio):
    video = studio.video()
    run = studio.project(video, translator="claude", review="full", review_engine="local", review_auto=True)
    line = lambda n, text, confidence: {"line": n, "corrected": text, "reason": "d'après le contexte", "confidence": confidence}  # noqa: E731
    studio.lines = [[line(5, "A disk can fail and a flat can burn down.", "high"), line(6, "With three copies you do not lose your photos.", "medium"),
                     line(8, "", "high")]]                             # sure, less sure, a line removed
    opened(page, studio, run).locator(".run-main").click()
    panel = page.locator(".translation-panel")                         # the cost of the translation is asked at once:
    panel.wait_for()                                                   # the proposals left do not come first
    names = page.locator(".names-panel")
    names.wait_for()
    assert "2 lignes à vérifier" in names.inner_text() and not names.get_by_role("button", name="Plus tard").count()
    assert page.locator(".applied-panel summary").inner_text() == "1 correction appliquée sans vous demander"
    assert not studio.srt(video, "fr") and ("translation", "claude") not in studio.asked       # nothing spent yet
    panel.get_by_role("button", name="Traduire", exact=True).click()
    state_is(page, "done")
    english, french = (" ".join(studio.srt(video, code).split()) for code in ("en", "fr"))     # a subtitle is on one or two lines
    assert "a flat can burn down." in english                          # the sure one, applied
    assert "lose your pictures." in english and "see you soon." in english       # the less sure one and the line removed: as transcribed
    assert "TR A disk can fail and a flat can burn down." in french
    assert names.is_visible()                                          # still to decide, the project done


def test_one_go_ahead_for_all_the_videos_before_a_paid_model(page, studio):
    one, two = studio.video("Episode 1.mp4"), studio.video("Episode 2.mp4")
    run = studio.project(one, two, translator="claude")
    opened(page, studio, run).locator(".run-main").click()
    panel = page.locator(".translation-panel")
    panel.wait_for()
    state_is(page, "awaiting_translation")
    assert studio.transcribed == ["Episode-1", "Episode-2"] and not studio.asked        # both transcribed, nothing spent
    panel.get_by_text(re.compile("transcrite")).wait_for()             # its estimate, asked to the service
    said = panel.inner_text()
    assert "2 vidéo(s) transcrite(s)" in said and re.search(r"Claude\s+(≈ \d+,\d\d|< 0,01) \$", said)   # what it costs, for the two
    panel.get_by_role("checkbox", name=re.compile("Ne plus me demander")).click()
    panel.get_by_role("button", name="Traduire", exact=True).click()
    state_is(page, "done")
    assert states(run) == ["done", "done"] and {a for a in studio.asked} == {("translation", "claude")}
    assert "TR Hello" in studio.srt(one, "fr") and "TR Hello" in studio.srt(two, "fr")
    assert run.options.get("spend_agreed") is True                     # not asked again in this project


def test_a_project_is_dubbed_up_to_its_mkv(page, studio):
    video = studio.video()
    cfg = json.loads(config.USER_FILE.read_text(encoding="utf-8"))
    studio.settings(dubbing={**cfg["dubbing"], "enabled": True})
    run = studio.project(video, stages=(Stage.SUBTITLES, Stage.DUBBING), stop="mux")
    opened(page, studio, run)
    assert page.locator(".run-main").inner_text().split() == ["Lancer", "Sous-titres", "+", "Doublage"]
    page.locator(".run-main").click()
    state_is(page, "done")
    assert states(run) == ["done"] and states(run, Stage.DUBBING) == ["done"]
    assert studio.dubbed == [("Episode 1.mp4", "fr")]                  # the translated language, not the one spoken
    mkv = steps.mkv_path(video)
    assert mkv.is_file() and mkv.stat().st_size > 1000 and video.is_file()             # made next to the video, which stays


def test_an_address_is_downloaded_then_subtitled(page, studio):
    opened(page, studio)
    page.locator("#v2-entries").fill(ADDRESS)
    page.get_by_role("button", name="Ajouter", exact=True).click()
    page.get_by_text("A talk online").first.wait_for()
    until(page, lambda: studio.manager.projects.all, "the project made by the page")
    run = next(iter(studio.manager.projects.all.values()))
    assert run.project.items[0].is_address and not studio.downloaded   # read, not downloaded yet
    page.locator(".run-main").click()
    state_is(page, "done")
    assert studio.downloaded == [ADDRESS] and states(run, Stage.DOWNLOAD) == ["done"] and states(run) == ["done"]
    video = next(iter(studio.root.rglob("A talk online.mp4")))
    assert "TR Hello" in studio.srt(video, "fr")                       # its subtitles next to the file downloaded


def test_a_video_run_from_its_line_then_the_project_go_on_by_themselves_once_the_names_are_decided(page, studio):
    """The case of the author, 21/11/2026: a video run from its line, then Run for the others; the review of the names
    stops the first one. Before: Run said "Resume", and once the names were decided a second go-ahead asked for a
    model, so that the two videos could end translated by two models."""
    one = studio.video("Episode 1.mp4")
    two = studio.video("Episode 2.mp4", said="Hello and welcome. Today we talk about the rule of three copies. Thank you for watching.")
    run = studio.project(one, two, review="names", review_engine="local")
    studio.review = [[FIX]]                                            # names in the first video only
    opened(page, studio, run)
    page.get_by_role("button", name="Sous-titrer", exact=True).first.click()      # the first video, from its line
    until(page, lambda: run.project.items[0].state(Stage.SUBTITLES) in (S.RUNNING, S.PAUSED), "the first video at work")
    run_button = page.locator(".run-main")
    run_button.filter(has_text="Lancer").wait_for()                    # never run: "Run", not "Resume"
    assert "Reprendre" not in run_button.inner_text()
    run_button.click()
    names = page.locator(".names-panel")
    names.wait_for()
    names.get_by_role("button", name="Valider et continuer").click()
    state_is(page, "done")                                             # no second go-ahead: the translator is free
    assert states(run) == ["done", "done"] and not page.locator(".translation-panel").count()
    assert [a for a in studio.asked if a[0] == "translation"] == [("translation", "local")] * 2      # one model for both
    assert "Gariaev" in studio.srt(one, "fr")


def test_one_video_is_taken_through_its_steps_from_its_menu(page, studio):
    one, two = studio.video("Episode 1.mp4"), studio.video("Episode 2.mp4")
    cfg = json.loads(config.USER_FILE.read_text(encoding="utf-8"))
    studio.settings(dubbing={**cfg["dubbing"], "enabled": True})
    run = studio.project(one, two)
    opened(page, studio, run)
    page.get_by_role("button", name="Toutes les actions pour cette vidéo").nth(1).click()
    page.get_by_role("menuitem", name=re.compile("^Sous-titrer \\+ doubler")).or_(page.get_by_text(re.compile("^Sous-titrer \\+ doubler"))).first.click()
    until(page, lambda: states(run, Stage.DUBBING)[1] == "done", "the second video subtitled then dubbed", seconds=20)
    assert states(run) == ["todo", "done"] and studio.dubbed == [("Episode 2.mp4", "fr")]           # the other one untouched
    assert steps.mkv_path(two).is_file() and not studio.srt(one, "fr")


def test_a_project_is_found_again_after_the_service_restarted(page, studio):
    one, two = studio.video("Episode 1.mp4"), studio.video("Episode 2.mp4")
    run = studio.project(one, two)
    studio.free.clear()
    opened(page, studio, run).locator(".run-main").click()
    page.get_by_role("button", name="Pause", exact=True).click()       # paused while the first video is translated
    studio.free.set()
    page.locator(".run-main").filter(has_text="Reprendre").wait_for()
    until(page, lambda: states(run) == ["done", "todo"], "the first video finished, the second left to do")
    studio.restart(page)                                               # the service stops and starts again: it knows no project
    page.tolerated.append(r"404 \(Not Found\).*/api/projects/")       # the page asks for the project it showed: unknown now
    page.goto(studio.url + "/")
    offered = page.get_by_role("button", name="Reprendre", exact=True)
    offered.wait_for()                                                 # the project of the last session, offered at the top
    offered.click()
    page.get_by_text("Episode 2.mp4").first.wait_for()
    again = next(iter(studio.manager.projects.all.values()))
    assert again.id == run.id and states(again) == ["done", "todo"]    # what was done is read from the disk
    page.locator(".run-main").click()
    state_is(page, "done")
    assert states(again) == ["done", "done"] and studio.transcribed == ["Episode-1", "Episode-2"]   # the first one not done twice


def test_the_settings_are_changed_on_their_page(page, studio):
    def saved():
        return json.loads(config.USER_FILE.read_text(encoding="utf-8"))
    page.goto(studio.url + "/settings")
    page.get_by_text("Transcription", exact=True).first.click()
    name = page.get_by_label("Nom", exact=True).locator("visible=true").first
    assert name.input_value() == "Serveur de test"                     # the server of the settings file of the test
    name.fill("Serveur du salon")
    page.get_by_role("button", name="Enregistrer").first.click()
    until(page, lambda: saved()["transcriptions"][0]["name"] == "Serveur du salon", "the name of the server saved")
    assert saved()["active_translator"] == "local" and [t["id"] for t in saved()["translators"]] == ["local", "claude", "none"]   # the rest kept
    assert saved()["translators"][1]["key"] == "env:ANTHROPIC_API_KEY"                  # a key stays a reference
