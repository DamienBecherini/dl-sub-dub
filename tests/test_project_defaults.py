"""The defaults of a new project chosen in the Settings ([projects]): the bottom layer of every project's settings."""
from subtitles.project import settings
from subtitles.project.model import Project


def test_projects_table_gives_the_defaults():
    cfg = {"target_languages": ["fr", "en"], "projects": {"order": "videos", "review": True, "replace": True, "pause": False, "mkv": False}}
    d = settings.resolve(cfg, Project("/tmp", [], {}, [], ""))
    assert (d["order"], d["review"], d["replace"], d["pause"], d["mkv"], d["languages"]) == ("videos", "names", True, None, False, ["fr", "en"])       # pause false ("never", of older versions): not set


def test_absent_or_wrong_values_keep_the_old_defaults():
    d = settings.resolve({"projects": {"order": "sideways", "pause": "yes"}}, Project("/tmp", [], {}, [], ""))
    assert (d["order"], d["review"], d["replace"], d["pause"], d["mkv"]) == ("steps", "", False, None, None)


def test_a_project_choice_wins_over_the_defaults():
    p = Project("/tmp", [], {}, [], "")
    p.settings["order"] = "steps"
    assert settings.resolve({"projects": {"order": "videos"}}, p)["order"] == "steps"
