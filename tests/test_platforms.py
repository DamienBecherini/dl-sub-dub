"""Dialog boxes and folder opening outside Windows: commands built from the system and the tools present."""
import os
import sys

import pytest

from subtitles.web import router
from subtitles.web import desktop


def having(*tools):
    return lambda name: f"/usr/bin/{name}" if name in tools else None


def test_zenity_video_file_with_filter_and_start():
    c = desktop.dialog_command("file", "/data/videos", "linux", having("zenity"))
    assert c[:2] == ["zenity", "--file-selection"]
    assert "--filename=/data/videos/" in c
    assert any(a.startswith("--file-filter=Vidéos") and "*.mkv" in a for a in c)
    assert "--directory" not in c
    assert "--multiple" in c and "--separator=\n" in c                     # several videos at once


def test_zenity_folder():
    c = desktop.dialog_command("folder", "", "linux", having("zenity", "kdialog"))
    assert c[0] == "zenity" and "--directory" in c and not any(a.startswith("--filename") for a in c) and "--multiple" not in c


def test_kdialog_if_no_zenity():
    assert desktop.dialog_command("project", "/x", "linux", having("kdialog"))[3:] == ["--getopenfilename", "/x", "*.project.json *.batch.json|Fichier de projet"]
    assert desktop.dialog_command("source_folder", "", "linux", having("kdialog"))[3:] == ["--getexistingdirectory", "."]
    assert desktop.dialog_command("file", "", "linux", having("kdialog"))[-2:] == ["--multiple", "--separate-output"]


def test_no_tool():
    assert desktop.dialog_command("file", "", "linux", having()) is None


def test_macos_path_passed_as_argument_not_in_the_script():
    start = '/a"; do shell script "x'
    c = desktop.dialog_command("file", start, "darwin", having())
    assert c[:2] == ["osascript", "-e"] and "choose file" in c[2]
    assert start not in c[2] and c[-1] == start
    assert "choose folder" in desktop.dialog_command("folder", "", "darwin", having())[2]
    assert "multiple selections allowed" in c[2]
    assert "multiple" not in desktop.dialog_command("project", "", "darwin", having())[2]


def test_without_tool_useful_message(monkeypatch):
    monkeypatch.setattr(desktop, "dialog_command", lambda *_: None)
    with pytest.raises(router.RequestError, match="saisir le chemin"):
        desktop._unix_dialog("file", "")


@pytest.mark.parametrize("code, out, expected", [(0, "/data/film.mp4\n", ["/data/film.mp4"]), (0, "/data/dossier/\n", ["/data/dossier"]),
                                                  (0, "/data/a.mp4\n/data/b.mkv\n", ["/data/a.mp4", "/data/b.mkv"]), (1, "", [])])
def test_dialog_result(monkeypatch, code, out, expected):
    monkeypatch.setattr(desktop, "dialog_command", lambda *_: ["tool"])
    monkeypatch.setattr(desktop.subprocess, "run", lambda *_a, **_k: type("R", (), {"returncode": code, "stdout": out})())
    assert desktop._unix_dialog("file", "") == expected


def test_open_depending_on_the_system(tmp_path):
    f = tmp_path / "a.srt"
    f.write_text("x", encoding="utf-8")
    assert desktop.open_command(f, "win32") == ["explorer", "/select,", str(f)]
    assert desktop.open_command(tmp_path, "win32") == ["explorer", str(tmp_path)]
    assert desktop.open_command(f, "darwin") == ["open", "-R", str(f)]
    assert desktop.open_command(tmp_path, "darwin") == ["open", str(tmp_path)]
    assert desktop.open_command(f, "linux") == ["xdg-open", str(tmp_path)]
    assert desktop.play_command(f, "darwin") == ["open", str(f)] and desktop.play_command(f, "linux") == ["xdg-open", str(f)]
    assert desktop.play_command(f, "win32") is None


TRAP = "D:/Vidéos d\u2019été\u2019; Write-Output INJECTED; \u2019 'x'"     # typographic and straight apostrophes


def test_windows_dialog_gets_its_values_as_data():
    """The starting folder, the title and the filter go through the environment, never into the PowerShell code: a
    folder named « d’été » (or by a video site, to run code) reaches the dialog box as it is."""
    for kind in ("file", "project_folder"):
        command, env = desktop.windows_dialog(kind, TRAP)
        assert env["SUBTITLES_DIALOG_START"] == TRAP and "Vidéos" not in command[-1] and "INJECTED" not in command[-1]
        assert ("SUBTITLES_DIALOG_FILTER" in env) == (kind == "file") and ("SUBTITLES_DIALOG_TITLE" in env) == (kind != "file")


@pytest.mark.skipif(sys.platform != "win32", reason="PowerShell of Windows")
def test_powershell_reads_the_folder_from_its_environment():
    import subprocess
    r = subprocess.run(["powershell", "-NoProfile", "-Command",
                        "[Console]::OutputEncoding = [Text.Encoding]::UTF8; Write-Output $env:SUBTITLES_DIALOG_START"],
                       capture_output=True, text=True, encoding="utf-8", env={**os.environ, "SUBTITLES_DIALOG_START": TRAP})
    assert r.returncode == 0 and r.stdout.strip() == TRAP
