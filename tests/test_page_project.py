"""The everyday paths of the page, in a real browser, on the real service (fixture `studio`, see page_site.py): a
project made from the page, run to its files, paused, resumed, stopped; one video left out, cancelled, failed and
tried again, removed; a language added; the page in English and on a phone."""
import re

from page_site import opened, until
from subtitles.project import Stage, StageState

S = StageState
STARTS = re.compile(r"(\d\d:\d\d:\d\d,\d\d\d) --> ")


def chip(page):
    return page.locator(".nav-bar .chip")


def state_is(page, key: str):
    """Waits for the state of the project shown at the bottom of the page ("done", "suspended", "running"…)."""
    page.locator(f".nav-bar .chip.state-{key}").wait_for()


def line(page, title: str):
    return page.locator(".video-line, li, article").filter(has_text=title).last


def states(run) -> list[str]:
    return [str(i.state(Stage.SUBTITLES)) for i in run.project.items]


def test_a_video_added_from_the_page_is_subtitled_and_translated(page, studio):
    video = studio.video("Episode 1.mp4")
    opened(page, studio)
    page.locator("#v2-entries").fill(video.as_posix())
    page.get_by_role("button", name="Ajouter", exact=True).click()
    page.get_by_text("Episode 1.mp4").first.wait_for()                  # the line of the video, the project made with it
    until(page, lambda: studio.manager.projects.all, "the project made by the page")   # the line shows at once, the project a moment later
    run = next(iter(studio.manager.projects.all.values()))
    assert chip(page).inner_text() == "prêt"
    page.locator(".run-main").click()
    state_is(page, "done")
    assert studio.transcribed == ["Episode-1"] and studio.asked == [("translation", "local")]
    english, french = studio.srt(video, "en"), studio.srt(video, "fr")
    assert "Hello and welcome" in english and "TR Hello and welcome" in french          # the files a player loads, next to the video
    assert len(STARTS.findall(english)) > 3 and STARTS.findall(english) == STARTS.findall(french)   # each line starts when it is said, in both
    assert states(run) == ["done"]
    assert page.locator(".run-main").inner_text().strip() == "Rien à lancer"            # done: nothing is done twice


def test_only_the_ticked_videos_are_run(page, studio):
    one, two = studio.video("Episode 1.mp4"), studio.video("Episode 2.mp4")
    run = studio.project(one, two)
    opened(page, studio, run)
    page.get_by_role("checkbox", name=re.compile("Episode 2")).first.click()
    until(page, lambda: not run.project.items[1].selected, "the second video unticked")
    page.locator(".run-main").click()
    state_is(page, "done")
    assert states(run) == ["done", "todo"] and studio.transcribed == ["Episode-1"]
    assert studio.srt(one, "fr") and not studio.srt(two, "fr")


def test_pause_lets_the_video_at_work_end_and_resume_goes_on(page, studio):
    one, two = studio.video("Episode 1.mp4"), studio.video("Episode 2.mp4")
    run = studio.project(one, two)
    studio.free.clear()                                                # the first translation lasts as long as the test wants
    opened(page, studio, run).locator(".run-main").click()
    page.get_by_role("button", name="Pause", exact=True).click()
    state_is(page, "suspended")                                        # said at once, while the first video ends
    assert states(run) == ["running", "todo"]
    studio.free.set()
    page.locator(".run-main").filter(has_text="Reprendre").wait_for()
    page.wait_for_timeout(500)
    assert states(run) == ["done", "todo"] and chip(page).inner_text() == "en pause"    # the second one was not started
    page.locator(".run-main").click()
    state_is(page, "done")
    assert states(run) == ["done", "done"] and studio.srt(two, "fr")


def test_stop_puts_the_video_back_and_resume_takes_it_again(page, studio):
    video = studio.video()
    run = studio.project(video)
    studio.free.clear()
    opened(page, studio, run).locator(".run-main").click()
    page.get_by_role("button", name="Arrêter", exact=True).click()
    page.locator(".run-main").filter(has_text="Reprendre").wait_for()
    assert states(run) == ["todo"] and not studio.srt(video, "fr")
    studio.free.set()
    page.locator(".run-main").click()
    state_is(page, "done")
    assert states(run) == ["done"] and studio.transcribed == ["Episode-1"]              # resumed from the transcription kept
    assert "TR Hello" in studio.srt(video, "fr")


def test_a_video_is_cancelled_from_its_line_then_tried_again(page, studio):
    video = studio.video()
    run = studio.project(video)
    studio.free.clear()
    opened(page, studio, run).locator(".run-main").click()
    page.get_by_role("button", name="Annuler", exact=True).click()     # the button of its line, while it is at work
    state_is(page, "done")                                             # nothing else to do in this project
    assert states(run) == ["cancelled"] and not studio.srt(video, "fr")
    studio.free.set()
    page.get_by_role("button", name="Réessayer", exact=True).click()
    page.locator(".run-main").filter(has_text="Rien à lancer").wait_for()
    assert states(run) == ["done"] and "TR Hello" in studio.srt(video, "fr")


def test_a_failed_translation_is_said_on_the_line_and_tried_again(page, studio):
    video = studio.video()
    run = studio.project(video)
    studio.failing = "le modèle ne répond plus"
    opened(page, studio, run).locator(".run-main").click()
    page.get_by_role("button", name="Réessayer", exact=True).wait_for()
    assert states(run) == ["error"] and "le modèle ne répond plus" in run.project.items[0].stages[Stage.SUBTITLES].error
    assert "le modèle ne répond plus" in page.locator("body").inner_text()              # the reason, on the page
    studio.failing = ""
    page.get_by_role("button", name="Réessayer", exact=True).click()
    page.locator(".run-main").filter(has_text="Rien à lancer").wait_for()
    assert states(run) == ["done"] and studio.transcribed == ["Episode-1"] and "TR Hello" in studio.srt(video, "fr")


def test_a_video_is_removed_from_the_list(page, studio):
    one, two = studio.video("Episode 1.mp4"), studio.video("Episode 2.mp4")
    run = studio.project(one, two)
    opened(page, studio, run)
    page.get_by_role("button", name="Retirer de la liste").nth(1).click()
    page.get_by_text("Episode 2.mp4").first.wait_for(state="detached")
    assert [i.title for i in run.project.items] == ["Episode 1.mp4"] and two.is_file()   # out of the list, its file untouched


def test_a_language_added_to_a_project_done_only_translates_it(page, studio):
    video = studio.video()
    run = studio.project(video)
    opened(page, studio, run).locator(".run-main").click()
    state_is(page, "done")
    page.get_by_text(re.compile("autres langues")).first.click()       # a few languages are shown, the others behind this
    page.get_by_role("checkbox", name="allemand").click()
    until(page, lambda: run.options.get("languages") == ["fr", "de"], "German added to the project")
    page.locator(".run-main").filter(has_text="Lancer").click()
    page.locator(".run-main").filter(has_text="Rien à lancer").wait_for()
    assert "TR Hello" in studio.srt(video, "de") and studio.transcribed == ["Episode-1"]  # not transcribed again
    assert run.options["languages"] == ["fr", "de"]


def test_the_page_in_english(page, studio):
    run = studio.project(studio.video())
    opened(page, studio, run)
    assert page.locator(".run-main").inner_text().split() == ["Lancer", "Sous-titres"]
    page.get_by_role("button", name=re.compile("Langue de l'interface")).click()
    page.get_by_role("menuitemradio", name="English").or_(page.get_by_role("button", name="English")).first.click()
    page.locator(".run-main").filter(has_text="Run").wait_for()
    assert page.locator(".run-main").inner_text().split() == ["Run", "Subtitles"] and chip(page).inner_text() == "ready"


def test_the_page_on_a_phone(page, studio):
    run = studio.project(studio.video("Episode 1.mp4"))
    page.set_viewport_size({"width": 390, "height": 800})
    opened(page, studio, run)
    assert page.get_by_text("Episode 1.mp4").first.is_visible() and page.locator(".run-main").is_visible()
    box = page.locator(".run-main").bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= 390            # the button of the bar stays on the screen
    page.locator(".run-main").click()
    state_is(page, "done")
