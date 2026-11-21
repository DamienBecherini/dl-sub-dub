"""What the Settings give a new project (New projects, `[projects]`): written in the project when the page makes it,
so that the work does what the page shows; a project nothing was chosen for has the plain defaults."""
import json
import time
import urllib.request
from pathlib import Path

import pytest

from subtitles import config, muxing, pipeline, steps
from subtitles.project import Stage, settings
from subtitles.project.model import Project


def test_the_projects_table_gives_what_a_new_project_starts_with():
    cfg = {"target_languages": ["fr", "en"], "projects": {"order": "videos", "review": True, "review_engine": "local", "review_auto": True,
                                                           "replace": True, "pause": True, "mkv": False, "dubbing": True}}
    assert settings.starting(cfg) == {"order": "videos", "review": "names", "review_engine": "local", "review_auto": True,
                                      "replace": True, "pause": True, "mkv": False}       # `dubbing`: a step ticked, not a setting


def test_absent_or_wrong_values_are_not_written():
    assert settings.starting({}) == {}
    assert settings.starting({"projects": {"order": "sideways", "pause": "yes", "review": "", "replace": False, "mkv": True, "review_auto": "yes"}}) == {}
    assert settings.starting({"projects": {"pause": False}}) == {}                        # "never", of older versions: not set


def test_a_project_nothing_was_chosen_for_has_the_plain_defaults():
    cfg = {"target_languages": ["fr", "en"], "projects": {"order": "videos", "review": "full", "replace": True, "pause": True, "mkv": False}}
    d = settings.resolve(cfg, Project("/tmp", [], {}, [], ""))                            # made before, or by a script: what the work does
    assert (d["order"], d["review"], d["review_auto"], d["replace"], d["pause"], d["mkv"], d["languages"]) == ("steps", "", False, False, None, None, ["fr", "en"])


def test_a_project_choice_wins():
    p = Project("/tmp", [], settings.starting({"projects": {"order": "videos"}}), [], "")
    assert settings.resolve({}, p)["order"] == "videos"
    p.settings["order"] = "steps"
    assert settings.resolve({"projects": {"order": "videos"}}, p)["order"] == "steps"


def made_by_the_page(studio, videos, stages) -> dict:
    """A project made as the page makes one (`POST /api/projects`): what the service answers."""
    items = [{"source": v.as_posix(), "title": v.name} for v in (videos if isinstance(videos, list) else [videos])]
    request = urllib.request.Request(studio.url + "/api/projects", method="POST", headers={"Content-Type": "application/json"},
                                     data=json.dumps({"items": items, "stages": stages}).encode())
    with urllib.request.urlopen(request, timeout=10) as answer:
        return json.loads(answer.read())


def with_dubbing(studio, **projects):
    """The dubbing turned on in the settings, and these choices for the new projects."""
    cfg = json.loads(config.USER_FILE.read_text(encoding="utf-8"))
    studio.settings(dubbing={**cfg["dubbing"], "enabled": True}, projects=projects)


def to_its_end(studio, shown: dict, seconds: float = 30):
    """Runs the project the page was answered with, and waits for it to be done."""
    run = studio.manager.projects.get(shown["id"])
    studio.manager.projects.start(run.id)
    for _ in range(int(seconds * 20)):
        if str(run.state) == "done":
            return run
        time.sleep(0.05)
    raise AssertionError(f"the project did not end: {run.state}, {[(str(i.state(Stage.SUBTITLES)), str(i.state(Stage.DUBBING))) for i in run.project.items]}")


@pytest.mark.parametrize("order, expected", [
    ("videos", ["transcribed Episode-1", "dubbed Episode-1", "transcribed Episode-2", "dubbed Episode-2"]),     # each video to its end
    ("steps", ["transcribed Episode-1", "transcribed Episode-2", "dubbed Episode-1", "dubbed Episode-2"])])     # every video through one step
def test_the_order_of_the_settings_is_the_order_of_the_work(studio, order, expected):
    with_dubbing(studio, order=order)
    videos = [studio.video("Episode 1.mp4"), studio.video("Episode 2.mp4")]
    to_its_end(studio, made_by_the_page(studio, videos, ["subtitles", "dubbing"]))
    assert studio.order == expected


def test_the_dubbed_track_alone_of_the_settings_makes_no_mkv(studio):
    with_dubbing(studio, mkv=False)
    video = studio.video()
    to_its_end(studio, made_by_the_page(studio, video, ["subtitles", "dubbing"]))
    work = pipeline.work_dir(studio.root / "out", video)
    assert steps.dubbing_file(work, "fr").is_file() and studio.dubbed == [("Episode 1.mp4", "fr")]      # the voice is made
    assert not steps.mkv_path(video).is_file() and video.is_file()                                      # and no MKV with it


def test_the_mkv_takes_the_place_of_the_video_when_the_settings_say_so(studio, monkeypatch):
    trashed = []                                                       # the recycle bin of the test: not the user's
    monkeypatch.setattr(muxing, "to_trash", lambda f: (trashed.append(Path(f)), Path(f).unlink(), True)[2])
    with_dubbing(studio, replace=True)
    video = studio.video()
    run = to_its_end(studio, made_by_the_page(studio, video, ["subtitles", "dubbing"]))
    final = Path(run.project.items[0].video)
    assert trashed == [video] and not video.is_file()                  # the original went to the recycle bin
    assert final == video.with_suffix(".mkv") and final.stat().st_size > 1000 and not steps.mkv_path(video).is_file()     # the MKV has its name


def test_a_project_to_dub_takes_the_final_file_of_the_settings(studio):
    studio.settings(projects={"mkv": False, "replace": True})
    shown = made_by_the_page(studio, studio.video(), ["subtitles", "dubbing"])
    run = studio.manager.projects.get(shown["id"])
    assert run.options["stop"] == "dubbing" and run.options["replace"] is True       # the dubbed track alone, in place of the video
    assert (shown["dubbing"]["mkv"], shown["dubbing"]["replace"]) == (False, True)   # as the page shows it


def test_a_project_made_by_the_page_does_what_the_settings_say(studio):
    """Before, the page showed these choices on a new project and the work ignored them (the review was not run)."""
    studio.settings(projects={"order": "videos", "review": "names", "review_engine": "local", "review_auto": True, "pause": True})
    video = studio.video()
    shown = made_by_the_page(studio, video, ["subtitles"])
    run = studio.manager.projects.get(shown["id"])
    assert run.options["review"] == "names" and run.by_video and shown["order"] == "videos"
    assert (shown["subtitles"]["review"], shown["subtitles"]["review_auto"], shown["subtitles"]["pause"]) == ("names", True, True)
    studio.manager.projects.start(run.id)
    for _ in range(300):
        if ("review", "local") in studio.asked and run.summary()["rules"]["go_ahead"]:
            break
        time.sleep(0.05)
    assert ("review", "local") in studio.asked                                          # the review of the Settings ran
    assert run.summary()["rules"]["go_ahead"] and not studio.srt(video, "fr")           # and the pause asked before a free translation
