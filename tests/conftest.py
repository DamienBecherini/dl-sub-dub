"""The tests check the French messages: language imposed, whatever the system's. They never touch the files of the
user folder (history, last project, speeds): an archived task would otherwise replace the real history."""
import time
from pathlib import Path

import pytest

from subtitles.web import last_project
from subtitles import i18n, source, tracks
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


@pytest.fixture(autouse=True)
def _no_site_reading(monkeypatch):
    """The projects read the information of the sites in the background: never for real in the tests (no network,
    no yt-dlp); the tests of that reading replace it with their own."""
    def offline(url, command):
        raise source.SourceError("no site reading in the tests")
    monkeypatch.setattr(tracks, "site_info", offline)


FAILURES_LOG = Path(__file__).parent / "failures.log"     # ignored by git


def pytest_runtest_logreport(report):
    """Each failure is written down with its date, so that a test that fails once in a while leaves a trace even when
    the next run passes: tests/failures.log (name of the test, phase, last line of the error)."""
    if report.failed:
        last = (report.longreprtext.strip().splitlines() or [""])[-1][:300]
        with FAILURES_LOG.open("a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {report.nodeid}  [{report.when}]  {last}\n")
