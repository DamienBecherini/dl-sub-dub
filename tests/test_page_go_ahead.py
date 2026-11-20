"""The page, in a real browser, when a video waits for the user before a model works: a transcription that looks
little like its language (the warning, the model asked for the language, the list of the user), a paid model (its cost
shown before the go-ahead, from the panel or from the line), a video run on its own from its line. The project is run
from the page and the service works for real (its queue, its scheduler, its routes) on a transcription kept on the
disk; only what leaves the machine is replaced (the model asked, the translation). Skipped without Playwright: see the
fixture `page` in conftest.py."""
import json
import re
import threading
from http.server import ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from subtitles import config, language, pipeline, translation
from subtitles.project import Stage, StageState
from subtitles.web import server, service
from subtitles.web.manager import Manager
from subtitles.web.tasks import TaskQueue
from test_language_doubt import BY_EAR, CLAUDE, FRENCH, LOCAL, Model, a_video, text

TITLE = "Episode 1.mp4"


@pytest.fixture
def site(tmp_path, monkeypatch):
    """A service of its own, at work (the tasks really run), with one project of one video not started yet: a free
    model is the translator, a paid one is in the settings too. Gives what the test looks at."""
    cfg = {"output_dir": str(tmp_path), "ui_language": "fr", "transcriptions": [{"id": "t", "name": "T", "url": "http://127.0.0.1:9"}],
           "active_transcription": "t", "translators": [LOCAL, CLAUDE], "active_translator": "local", "target_languages": ["fr"],
           "cleanup": {}}
    monkeypatch.setattr(config, "load", lambda *_a, **_k: json.loads(json.dumps(cfg)))
    model, translated, free = Model("ar"), [], threading.Event()
    free.set()                                                                 # cleared by a test: the translation waits for it
    monkeypatch.setattr(translation, "create", lambda m, log=print: model)

    def translate(r, p, log, step, cancelled):
        assert free.wait(20)
        translated.append(p.code)
        return pipeline.Result(p.video, p.source_srt, p.source_srt, p.code, len(p.cues), True)
    monkeypatch.setattr(pipeline, "translate_and_copy", translate)
    g = Manager(TaskQueue(history=[]))
    monkeypatch.setattr(service, "manager", g)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    def project(said: str, **options):
        video = a_video(tmp_path, text(said))
        return g.projects.create(tmp_path.as_posix(), [{"path": video.as_posix(), "title": TITLE}], {"translator": "local", **options}, "Mine",
                                 stages=[Stage.SUBTITLES], start=False)
    yield SimpleNamespace(url=f"http://127.0.0.1:{httpd.server_address[1]}", project=project, model=model, translated=translated, free=free)
    free.set()
    g.close()
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture
def page(site, page):
    """The page of conftest.py, opened after the service and closed before it stops: a request still on its way when
    the service stops would be counted as an error of the page (seen on Linux)."""
    return page


def opened(page, site, run):
    """The page on this project (it keeps the project it shows in the browser), ready to be run."""
    page.add_init_script("localStorage.setItem('v2-page', JSON.stringify({projectId: '%s', view: 'compact'}))" % run.id)
    page.goto(site.url + "/")
    page.locator(".run-main").wait_for()
    return page


def run_until_the_warning(page, site, run):
    opened(page, site, run).locator(".run-main").click()
    doubts = page.locator(".translation-panel .doubts")
    doubts.wait_for()
    return doubts


def test_a_doubtful_video_waits_with_its_warning_and_is_cancelled_from_it(page, site):
    run = site.project(BY_EAR)
    doubts = run_until_the_warning(page, site, run)
    said = doubts.inner_text()
    assert "Langue douteuse" in said and TITLE in said
    assert re.search(r"Seulement 7 % de ses 260 mots .*\(italien\).* 15 %", said)
    assert str(language.USER_LISTS) in said                                   # where a list is added, for an engine that knows more
    assert page.locator(".nav-bar .chip").inner_text() == "en attente de votre accord"
    assert not site.model.asked and not site.translated                       # nothing was asked to a model, free or not
    item = run.project.items[0]
    assert item.state(Stage.SUBTITLES) == StageState.PAUSED

    page.get_by_role("button", name="Donner l'accord…").click()               # the button of its line says it too, before going on
    dialog = page.locator("dialog.confirm-dialog[open]")
    assert "Langue douteuse" in dialog.inner_text() and "7 %" in dialog.inner_text()
    dialog.get_by_role("button", name="Annuler").click()
    assert item.state(Stage.SUBTITLES) == StageState.PAUSED and not site.translated

    doubts.get_by_role("button", name="Annuler cette vidéo").click()
    page.locator(".translation-panel").wait_for(state="detached")
    assert item.state(Stage.SUBTITLES) == StageState.CANCELLED and not site.model.asked and not site.translated


def test_a_model_is_asked_for_the_language_from_the_warning_then_the_user_goes_on(page, site):
    run = site.project(BY_EAR)
    doubts = run_until_the_warning(page, site, run)
    choices = doubts.locator("select option").all_inner_texts()
    assert ["gratuit" in c for c in choices] == [True, False] and "< 0,01 $" in choices[1]     # the cost of the question, before asking
    doubts.locator("select").select_option("claude")
    doubts.get_by_role("button", name="Demander", exact=True).click()
    opinion = doubts.locator(".opinion")
    opinion.wait_for()
    assert opinion.inner_text() == "D'après « Claude », la langue parlée est : arabe."
    assert len(site.model.asked) == 1 and site.model.asked[0][0] == language.ASK_SPOKEN
    assert run.project.items[0].state(Stage.SUBTITLES) == StageState.PAUSED and not site.translated     # an opinion: nothing goes on by itself

    page.locator(".translation-panel").get_by_role("button", name="Traduire").click()           # going on anyway
    page.locator(".translation-panel").wait_for(state="detached")
    page.locator(".nav-bar .chip.state-done").wait_for()
    assert site.translated == ["it"] and run.project.items[0].state(Stage.SUBTITLES) == StageState.DONE


def test_a_right_transcription_and_a_list_of_the_user_do_not_wait(page, site):
    run = site.project(FRENCH)
    opened(page, site, run).locator(".run-main").click()
    page.locator(".nav-bar .chip.state-done").wait_for()
    assert site.translated == ["fr"] and not page.locator(".translation-panel").count()


def run_from_its_line(page, site, run):
    """The video alone, from the button of its line: the project itself is not run."""
    opened(page, site, run).locator(".v2-line, li").get_by_role("button", name=re.compile("^(Sous-titrer|Traduire)$")).first.click()


def test_a_video_run_from_its_line_shows_its_cost_before_a_paid_model(page, site):
    run = site.project(FRENCH, translator="claude", languages=["en"])           # a French video to put into English
    run_from_its_line(page, site, run)
    panel = page.locator(".translation-panel")
    panel.wait_for()
    assert not panel.locator(".doubts").count() and "à traduire en anglais" in panel.inner_text()
    chosen = panel.locator("label.check:has(input[name='v2-translator']:checked)")
    assert re.search(r"Claude\s+≈ \d+,\d\d \$", chosen.inner_text())          # the paid model and its cost, in the panel of the project
    assert page.locator(".nav-bar .chip").inner_text() == "en attente de votre accord"
    assert page.locator(".nav-bar").get_by_role("button", name="Voir la demande").is_visible()
    assert not site.translated

    page.get_by_role("button", name="Donner l'accord…").click()               # from its line: what it starts and its cost first
    dialog = page.locator("dialog.confirm-dialog[open]")
    assert re.search(r"Traduction en anglais par « Claude » : ≈ \d+,\d\d \$", dialog.inner_text())      # written as the panel writes it
    assert not site.translated                                                # opening the question spends nothing
    dialog.get_by_role("button", name="Donner l'accord", exact=True).click()
    panel.wait_for(state="detached")
    page.locator(".nav-bar").get_by_role("button", name="Voir la demande").wait_for(state="detached")
    assert site.translated == ["fr"] and run.project.items[0].state(Stage.SUBTITLES) == StageState.DONE


def test_run_waits_while_the_only_video_is_at_work_on_its_own(page, site):
    site.free.clear()                                                         # the translation lasts as long as the test wants
    run = site.project(FRENCH)
    run_from_its_line(page, site, run)
    button = page.locator(".run-main")
    button.filter(has_text="Rien d'autre à lancer").wait_for()
    assert button.is_disabled() and not site.translated
    assert page.locator(".nav-bar .chip").inner_text() == "en cours"          # a project never run: at work, not "paused"
    site.free.set()
    button.filter(has_text="Rien à lancer").wait_for()
    assert button.is_disabled() and site.translated == ["fr"]
    assert page.locator(".nav-bar .chip").inner_text() == "terminé"


def test_a_list_added_by_the_user_is_read_at_once(page, site):
    """The engine knows a language the tool has no list for: its list, put in the user's folder while the service
    runs, makes its transcriptions right."""
    language.USER_LISTS.mkdir(parents=True)
    (language.USER_LISTS / "it.txt").write_text("# the words of the sample, as a list of the user\nfil meja rabbina wahed fi minnu insan\n", encoding="utf-8")
    run = site.project(BY_EAR)
    opened(page, site, run).locator(".run-main").click()
    page.locator(".nav-bar .chip.state-done").wait_for()
    assert site.translated == ["it"] and not page.locator(".translation-panel").count()
