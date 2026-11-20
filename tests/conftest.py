"""The tests check the French messages: language imposed, whatever the system's. They never touch the files of the
user folder (history, last project, speeds): an archived task would otherwise replace the real history."""
import os
import time
from pathlib import Path

import pytest

from subtitles.web import last_project
from subtitles import i18n, language, source, tracks
from subtitles.web import tasks, timing


@pytest.fixture(autouse=True)
def _french_language():
    i18n.set_language("fr")
    yield
    i18n.set_language(None)


@pytest.fixture(autouse=True)
def _user_files_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(tasks, "HISTORY_FILE", tmp_path / "user" / "history.json")
    monkeypatch.setattr(last_project, "FILE", tmp_path / "user" / "last_project.json")
    monkeypatch.setattr(timing, "FILE", tmp_path / "user" / "speeds.json")
    monkeypatch.setattr(timing, "_memory", None)
    monkeypatch.setattr(language, "USER_LISTS", tmp_path / "user" / "function_words")     # the lists of the tool alone


@pytest.fixture(autouse=True)
def _no_site_reading(monkeypatch):
    """The projects read the information of the sites in the background: never for real in the tests (no network,
    no yt-dlp); the tests of that reading replace it with their own."""
    def offline(url, command):
        raise source.SourceError("no site reading in the tests")
    monkeypatch.setattr(tracks, "site_info", offline)


# --- the page in a real browser (tests/test_page_*.py) ---------------------------------------------------------------

@pytest.fixture(scope="session")
def _browser():
    """A browser driven by Playwright, for the tests of the page: `pip install ".[ui]"`. The one Playwright installed
    (`playwright install chromium`) if there is one, otherwise the Chrome or Edge of the machine. Without Playwright
    or without any browser, these tests are skipped: the rest of the suite needs neither. PAGE_TESTS=required (the
    CI) makes them fail instead: a job meant to run them must not pass by skipping them."""
    required = os.environ.get("PAGE_TESTS") == "required"
    if required:
        import playwright.sync_api as sync_api
    else:
        sync_api = pytest.importorskip("playwright.sync_api", reason='tests of the page: pip install ".[ui]"')
    with sync_api.sync_playwright() as p:
        browser = None
        for channel in (None, "chrome", "msedge"):
            try:
                browser = p.chromium.launch(channel=channel)
                break
            except sync_api.Error:
                continue
        if browser is None:
            (pytest.fail if required else pytest.skip)("tests of the page: no browser (playwright install chromium, or Chrome, or Edge)")
        yield browser
        browser.close()


@pytest.fixture
def page(_browser):
    """A page of its own for each test (nothing kept from one to the next), in French, which fails the test on an
    error of the page's code (an exception not caught, a console error)."""
    context = _browser.new_context(locale="fr-FR", viewport={"width": 1100, "height": 900})
    context.set_default_timeout(10_000)
    p = context.new_page()
    errors = []
    p.on("pageerror", lambda e: errors.append(str(e)))
    p.on("console", lambda m: errors.append(f"{m.text} ({m.location.get('url', '')})") if m.type == "error" else None)
    yield p
    context.close()
    assert not errors, errors


FAILURES_LOG = Path(__file__).parent / "failures.log"     # ignored by git


def pytest_runtest_logreport(report):
    """Each failure is written down with its date, so that a test that fails once in a while leaves a trace even when
    the next run passes: tests/failures.log (name of the test, phase, last line of the error)."""
    if report.failed:
        last = (report.longreprtext.strip().splitlines() or [""])[-1][:300]
        with FAILURES_LOG.open("a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {report.nodeid}  [{report.when}]  {last}\n")
