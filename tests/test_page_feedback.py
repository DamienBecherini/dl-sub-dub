"""« Give my opinion », in a real browser on the real service (fixture `studio`, see page_site.py): the window, what it
will send and where, nothing of the user's work in it, a security flaw never sent to the public form, and the setting
that hides the button of the header (it stays in the footer). Also: the form of the repository and the window agree on
the fields, and what the service gives for the technical information."""
import json
import re
import urllib.parse
import urllib.request
from pathlib import Path

from page_site import opened, until
from subtitles import config

ROOT = Path(__file__).resolve().parents[1]


def window(page):
    page.locator("header").get_by_role("button", name="Donner mon avis").click()
    d = page.locator("dialog.feedback-dialog")
    d.wait_for()
    return d


def test_an_opinion_goes_to_the_form_of_the_repository_or_by_mail(page, studio):
    opened(page, studio)
    d = window(page)
    assert d.locator("#feedback-issue").get_attribute("aria-disabled") == "true"        # nothing to send yet
    d.locator(".stars label").nth(3).click()                                            # 4 out of 5
    d.get_by_role("radio", name="Une idée").click()
    d.get_by_label("Votre message").fill("Pouvoir trier les vidéos par durée.")
    issue = d.locator("#feedback-issue").get_attribute("href")
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(issue).query)
    assert issue.startswith("https://github.com/DamienBecherini/dl-sub-dub/issues/new?")
    assert (q["template"], q["labels"], q["rating"], q["type"]) == (["feedback.yml"], ["feedback"], ["★★★★☆ 4/5"], ["Idea"])
    assert q["message"] == ["Pouvoir trier les vidéos par durée."] and q["version"][0] and "environment" not in q
    mail = d.locator("#feedback-mail").get_attribute("href")
    assert mail.startswith("mailto:dl-sub-dub@damien.becherini.fr?subject=Feedback%20on%20dl-sub-dub")
    d.get_by_label("Joindre les informations techniques").check()
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(d.locator("#feedback-issue").get_attribute("href")).query)
    said = q["environment"][0]
    assert "Python" in said and "translator openai local" in said and "browser" in said
    preview = d.locator(".feedback-preview").inner_text()
    for private in (str(studio.root), "127.0.0.1", "Serveur de test", "Local"):          # nothing of the user's settings or machine
        assert private not in said and private not in preview


def test_a_security_flaw_is_never_sent_to_the_public_form(page, studio):
    opened(page, studio)
    d = window(page)
    d.get_by_role("button", name="la signaler en privé").click()
    assert not d.locator("#feedback-issue").count() and not d.locator(".stars").count()  # no public issue, no rating
    d.get_by_label("Votre message").fill("La clé s'affiche dans le journal.")
    assert d.locator("#feedback-private").get_attribute("href") == "https://github.com/DamienBecherini/dl-sub-dub/security/advisories/new"
    assert "Security%20flaw%20in%20dl-sub-dub" in d.locator("#feedback-mail").get_attribute("href")
    assert d.get_by_label("Joindre les informations techniques").is_checked()            # rarely understood without them


def test_the_button_of_the_header_can_be_hidden_and_stays_in_the_footer(page, studio):
    opened(page, studio)
    assert page.locator("header .feedback-button").count() == 1 and page.locator("footer .feedback-button").count() == 1
    page.goto(studio.url + "/settings")
    page.get_by_text("Application", exact=True).first.click()
    box = page.get_by_label(re.compile("Bouton « Donner mon avis » dans l'en-tête"))
    assert box.is_checked()
    box.uncheck()
    page.get_by_role("button", name="Enregistrer").first.click()
    until(page, lambda: json.loads(config.USER_FILE.read_text(encoding="utf-8")).get("web", {}).get("feedback_button") is False,
          "the setting saved")
    opened(page, studio)
    page.locator("footer .feedback-button").wait_for()
    assert not page.locator("header .feedback-button").count()                          # hidden in the header only
    page.locator("footer .feedback-button").click()
    page.locator("dialog.feedback-dialog").wait_for()


def test_the_window_fills_in_the_fields_the_issue_form_has():
    form = (ROOT / ".github" / "ISSUE_TEMPLATE" / "feedback.yml").read_text(encoding="utf-8")
    script = (ROOT / "subtitles" / "web" / "static" / "v2" / "feedback.js").read_text(encoding="utf-8")
    ids = re.findall(r"^\s+id: (\w+)$", form, re.M)
    assert ids == ["rating", "type", "message", "version", "environment"]
    assert all(re.search(rf"out\.{i} = ", script) for i in ids) and 'template: "feedback.yml"' in script
    assert "labels: [feedback]" in form and 'labels: "feedback"' in script


def test_the_technical_information_says_kinds_never_places(studio):
    with urllib.request.urlopen(studio.url + "/api/about", timeout=10) as r:
        about = json.loads(r.read())
    assert about["repository"] == "https://github.com/DamienBecherini/dl-sub-dub" and about["feedback_email"].startswith("dl-sub-dub@")
    assert (about["transcription"], about["translator"], about["dubbing"]) == ("local", "openai local", False)
    assert about["version"] and about["python"] and about["system"]
    assert "127.0.0.1" not in json.dumps(about) and str(studio.root) not in json.dumps(about)
