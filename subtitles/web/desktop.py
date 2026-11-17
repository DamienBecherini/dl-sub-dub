"""What the page asks of the desktop of the machine the service runs on: a dialog box to choose a file or a folder
(PowerShell on Windows, osascript on macOS, zenity or kdialog elsewhere), a folder shown in the file manager, a
video played in the default player."""
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

from .. import i18n
from .router import RequestError


_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_ONE_DIALOG = threading.Lock()                 # one dialog box at a time, whatever asks (several clicks, tabs, pages)
_open_dialog: subprocess.Popen | None = None   # the one open, to bring it to the front
_TITLES = {"file": "dialog.pick_video", "project": "dialog.pick_project", "source_folder": "dialog.source_folder",
           "download_folder": "dialog.download_folder", "project_folder": "dialog.project_folder",
           "addresses": "dialog.pick_addresses"}   # any other kind: the output folder
_VIDEOS = "*.mp4 *.mkv *.webm *.mov *.avi *.m4a *.mp3 *.opus *.ogg *.wav *.flac"
_PATTERNS = {"file": (_VIDEOS, "dialog.videos_audio"), "project": ("*.project.json *.batch.json", "dialog.project_file"),
             "addresses": ("*.txt", "dialog.text_files")}
_MULTI = {"file", "addresses"}                 # several files at once (Ctrl, Shift in the dialog box)
_MAC_SCRIPT = ("on run argv\n"
               "if (count of argv) > 1 then return POSIX path of (choose {what} with prompt (item 1 of argv) "
               "default location ((POSIX file (item 2 of argv)) as alias))\n"
               "return POSIX path of (choose {what} with prompt (item 1 of argv))\n"
               "end run")
_MAC_SCRIPT_MULTI = ("on run argv\n"
                     "if (count of argv) > 1 then\n"
                     "set l to (choose file with prompt (item 1 of argv) default location ((POSIX file (item 2 of argv)) as alias) "
                     "with multiple selections allowed)\n"
                     "else\n"
                     "set l to (choose file with prompt (item 1 of argv) with multiple selections allowed)\n"
                     "end if\n"
                     "set out to \"\"\n"
                     "repeat with f in l\n"
                     "set out to out & (POSIX path of f) & linefeed\n"
                     "end repeat\n"
                     "return out\n"
                     "end run")


def dialog_command(kind: str, start: str = "", platform: str = sys.platform, find=shutil.which) -> list[str] | None:
    """Dialog box outside Windows: osascript on macOS, zenity or kdialog elsewhere; None if none is installed."""
    title, pattern = i18n.t(_TITLES.get(kind, "dialog.output_folder")), _PATTERNS.get(kind)
    if pattern:
        pattern = (pattern[0], i18n.t(pattern[1]))
    multi = kind in _MULTI
    if platform == "darwin":
        script = _MAC_SCRIPT_MULTI if multi else _MAC_SCRIPT.format(what="file" if pattern else "folder")
        return ["osascript", "-e", script, title] + ([start] if start else [])
    if find("zenity"):
        c = ["zenity", "--file-selection", f"--title={title}"] + (["--multiple", "--separator=\n"] if multi else [])
        if start:
            c.append(f"--filename={start.rstrip('/')}/")
        if pattern:
            return c + [f"--file-filter={pattern[1]} | {pattern[0]}", f"--file-filter={i18n.t('dialog.all_files')} | *"]
        return c + ["--directory"]
    if find("kdialog"):
        if pattern:
            return ["kdialog", "--title", title, "--getopenfilename", start or ".", f"{pattern[0]}|{pattern[1]}"] \
                + (["--multiple", "--separate-output"] if multi else [])
        return ["kdialog", "--title", title, "--getexistingdirectory", start or "."]
    return None


def _paths(out: str) -> list[str]:
    """The paths a dialog box wrote, one per line (a folder without its last slash)."""
    return [p.rstrip("/") or p for p in (line.strip() for line in out.splitlines()) if p]


def _run(command: list[str], **kw) -> tuple[int, str]:
    """Runs the program of a dialog box, kept in `_open_dialog` while it is open; its code and what it wrote."""
    global _open_dialog
    p = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, text=True,
                         encoding="utf-8", errors="replace", **kw)
    _open_dialog = p
    try:
        out, _ = p.communicate(timeout=600)
    except subprocess.TimeoutExpired:
        p.kill()
        out, _ = p.communicate()
    finally:
        _open_dialog = None
    return p.returncode, out or ""


def _to_front(p: subprocess.Popen | None):
    """Brings the dialog box already open to the front (Windows: its windows, found by the process that shows them;
    Windows may only make its button flash in the taskbar)."""
    if p is None or os.name != "nt":
        return
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(h, _):
        pid = wintypes.DWORD()
        u.GetWindowThreadProcessId(h, ctypes.byref(pid))
        if pid.value == p.pid and u.IsWindowVisible(h):
            u.ShowWindow(h, 9)                                 # SW_RESTORE
            u.SetForegroundWindow(h)
        return True
    u.EnumWindows(each, 0)


def _unix_dialog(kind: str, start: str) -> list[str]:
    command = dialog_command(kind, start)
    if command is None:
        raise RequestError(501, i18n.t("err.no_dialog"))
    code, out = _run(command)
    return _paths(out) if code == 0 else []                  # non-zero code: choice cancelled


def dialog(kind: str, start: str = "") -> list[str]:
    """System dialog box, in the foreground (Windows: PowerShell in STA mode): the paths chosen (several for the
    videos and the lists of addresses), none if cancelled. One at a time: while one is open, it is brought to the
    front and the request is refused (409), instead of a second box over the first."""
    if not _ONE_DIALOG.acquire(blocking=False):
        _to_front(_open_dialog)
        raise RequestError(409, i18n.t("err.dialog_open"))
    try:
        if os.name != "nt":
            return _unix_dialog(kind, start)
        command, env = windows_dialog(kind, start)
        _code, out = _run(command, creationflags=_NO_WINDOW, env={**os.environ, **env})
        return _paths(out.replace("\\", "/"))
    finally:
        _ONE_DIALOG.release()


def windows_dialog(kind: str, start: str = "") -> tuple[list[str], dict]:
    """The PowerShell of a dialog box, and what it reads from its environment: the starting folder, the title and the
    filter are data, never pasted into the code (PowerShell also ends a '…' string at a typographic apostrophe ’, as
    in « d’été »: a folder named by a video site could have run code)."""
    env = {"SUBTITLES_DIALOG_START": str(start or "")}
    if kind in _PATTERNS:
        pattern, label = _PATTERNS[kind]
        env["SUBTITLES_DIALOG_FILTER"] = f"{i18n.t(label)}|{pattern.replace(' ', ';')}|{i18n.t('dialog.all_files')}|*.*"
        body = ("$d = New-Object System.Windows.Forms.OpenFileDialog; "
                f"$d.Filter = $env:SUBTITLES_DIALOG_FILTER; $d.Multiselect = ${'true' if kind in _MULTI else 'false'}; "
                "if ($env:SUBTITLES_DIALOG_START) { $d.InitialDirectory = $env:SUBTITLES_DIALOG_START }; "
                "if ($d.ShowDialog($f) -eq 'OK') { $d.FileNames -join \"`n\" }")
    else:
        env["SUBTITLES_DIALOG_TITLE"] = i18n.t(_TITLES.get(kind, "dialog.output_folder"))
        body = ("$d = New-Object System.Windows.Forms.FolderBrowserDialog; $d.Description = $env:SUBTITLES_DIALOG_TITLE; "
                "if ($env:SUBTITLES_DIALOG_START) { $d.SelectedPath = $env:SUBTITLES_DIALOG_START }; "
                "if ($d.ShowDialog($f) -eq 'OK') { $d.SelectedPath }")
    script = ("[Console]::OutputEncoding = [Text.Encoding]::UTF8; Add-Type -AssemblyName System.Windows.Forms; "
              "$f = New-Object System.Windows.Forms.Form; $f.TopMost = $true; $f.StartPosition = 'CenterScreen'; " + body)
    return ["powershell", "-NoProfile", "-STA", "-Command", script], env


def open_command(p: Path, platform: str = sys.platform) -> list[str]:
    """Shows a folder, or a file in its folder (selected when the system allows it)."""
    if platform == "win32":
        return ["explorer", str(p)] if p.is_dir() else ["explorer", "/select,", str(p)]
    if platform == "darwin":
        return ["open", str(p)] if p.is_dir() else ["open", "-R", str(p)]
    return ["xdg-open", str(p if p.is_dir() else p.parent)]


PLAYABLE = {e.strip("*") for e in _VIDEOS.split()}           # what "Open" may play: videos and sounds, nothing else


def play_command(p: Path, platform: str = sys.platform) -> list[str] | None:
    """Plays a video in the default player of the system (None on Windows: os.startfile, without a shell)."""
    if platform == "win32":
        return None
    return ["open", str(p)] if platform == "darwin" else ["xdg-open", str(p)]


def open_path(path: str, play: bool = False):
    p = Path(path)
    if not p.exists():
        raise RequestError(404, i18n.t("err.not_found_path", path=path))
    if play and (not p.is_file() or p.suffix.lower() not in PLAYABLE):
        raise RequestError(400, i18n.t("err.not_a_video", path=path))
    try:
        if play and play_command(p) is None:
            os.startfile(p)                                    # noqa: S606 (Windows: the associated player)
            return
        subprocess.Popen(play_command(p) if play else open_command(p))
    except OSError as e:
        raise RequestError(501, i18n.t("err.cannot_open", error=e)) from e
