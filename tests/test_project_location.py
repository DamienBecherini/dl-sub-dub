"""The name and the place of a project: a default before anything is added, renamed at any time, moved (its file,
its empty folder removed) while nothing has started, never after."""
import json

import pytest

from helpers import at
from subtitles.web import background
from subtitles.web import observed
from subtitles import config, source
from subtitles.project import Stage, StageState, sources, store
from subtitles.web.manager import Manager
from subtitles.web.tasks import TaskQueue


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    c = {"output_dir": str(tmp_path / "out"), "youtube": {"command": ["yt-dlp"], "download_dir": str(tmp_path / "dl")}}
    monkeypatch.setattr(config, "load", lambda: c)
    return c


@pytest.fixture
def g(cfg, monkeypatch):
    g = Manager(TaskQueue(history=[]), start=False)
    monkeypatch.setattr(observed, "measure_durations", lambda *_a: None)
    monkeypatch.setattr(background, "spawn", lambda *_a: None)
    return g


def test_a_default_before_anything_is_added(cfg):
    assert sources.default_location([], cfg) == (source.download_dir(cfg), True)


def new_project(g, tmp_path):
    root = tmp_path / "dl" / "Cours"
    root.mkdir(parents=True)
    return g.projects.create(root.as_posix(), [{"source": "https://x.example/a"}], {}, title="Cours", start=False)


def test_renamed_and_moved_while_nothing_has_started(g, tmp_path):
    run = new_project(g, tmp_path)
    old = run.file
    assert old.is_file() and not run.started()
    assert g.projects.relocate(run.id, "Saison 2", (tmp_path / "ailleurs").as_posix(), True) == ""
    assert run.root == (tmp_path / "ailleurs" / "Saison 2").as_posix() and run.project.title == "Saison 2"
    assert run.file == tmp_path / "ailleurs" / "Saison 2" / store.FILE_NAME and run.file.is_file()
    assert json.loads(run.file.read_text(encoding="utf-8"))["folder"] == run.root
    assert not old.exists() and not old.parent.exists()                         # the empty folder left is removed


def test_the_folder_left_stays_when_something_else_is_in_it(g, tmp_path):
    run = new_project(g, tmp_path)
    (tmp_path / "dl" / "Cours" / "mine.txt").write_text("x")
    assert g.projects.relocate(run.id, "Cours", (tmp_path / "dl").as_posix(), False) == ""
    assert run.root == (tmp_path / "dl").as_posix() and (tmp_path / "dl" / "Cours" / "mine.txt").exists()


def test_only_renamed_once_started(g, tmp_path):
    run = new_project(g, tmp_path)
    at(run.project.items[0], Stage.DOWNLOAD, StageState.DONE)
    assert run.summary()["started"]
    assert g.projects.relocate(run.id, "Cours", (tmp_path / "ailleurs").as_posix(), True) == "err.project_started_move"
    assert run.root == (tmp_path / "dl" / "Cours").as_posix()
    assert g.projects.relocate(run.id, "Cours, saison 2") == ""                  # the name alone: always
    assert run.project.title == "Cours, saison 2" and run.root == (tmp_path / "dl" / "Cours").as_posix()


def test_not_over_another_project_nor_to_a_relative_folder(g, tmp_path):
    run = new_project(g, tmp_path)
    other = tmp_path / "autre"
    other.mkdir()
    (other / store.FILE_NAME).write_text("{}")
    assert g.projects.relocate(run.id, "Cours", other.as_posix(), False) == "err.project_already_there"
    assert g.projects.relocate(run.id, "Cours", "relatif/dossier", False) == "err.folder_not_absolute"
    assert run.file.is_file()
