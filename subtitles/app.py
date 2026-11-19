"""dl-sub-dub as a desktop application: the page in a window of its own (pywebview, `pip install ".[app]"`).

    python -m subtitles.app                     opens the window (starts the service if it does not run)
    python -m subtitles.app --shortcut          creates the shortcut (Windows: Start menu; Linux: menu of the applications)
    python -m subtitles.app --shortcut desktop  on the desktop too
    python -m subtitles.app --shortcut remove   removes them

A service already running (started by hand, or by a supervision) is used and never stopped. Started here, it is
started with --app, so it says so in GET /api/health: when the window closes, it is told (POST /api/window) and stops
as soon as its work is done. A second start brings the open window to the front (Windows; elsewhere it does nothing).
Without pywebview, the page opens in the default browser. The size and place of the window, the storage of the page
(theme, view) and the log of the service started here are in the user folder of the settings.
"""
import argparse
import ctypes
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

from . import config, i18n

TITLE = "dl-sub-dub"
APP_ID = "dl-sub-dub.app"          # the same on the window and on the shortcut: grouped in the taskbar of Windows
ICONS = Path(__file__).with_name("icons")
WINDOWS = sys.platform == "win32"
GEOMETRY = "window.json"
LOG = "app-service.log"


def port() -> int:
    try:
        return int(config.load().get("web", {}).get("port", 4420))
    except (config.ConfigError, OSError, ValueError):
        return 4420


def call(p: int, path: str, body: dict | None = None, timeout: float = 1.0) -> dict | None:
    """GET (or POST with a body) on the service; None if nothing answers."""
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{p}{path}", data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except (OSError, ValueError):
        return None


def ours(health: dict | None) -> bool:
    return bool(health) and health.get("service") == "subtitles"


def start_service(p: int) -> bool:
    """The service without a console, detached: it outlives the window while it works."""
    log = config.state_dir() / LOG
    log.parent.mkdir(parents=True, exist_ok=True)
    out = open(log, "a", encoding="utf-8")
    out.write(f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
    out.flush()
    flags = {"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP} if WINDOWS else {"start_new_session": True}
    subprocess.Popen([sys.executable, "-m", "subtitles.web", "--port", str(p), "--app"], stdin=subprocess.DEVNULL, stdout=out,
                     stderr=subprocess.STDOUT, **flags)
    out.close()
    for _ in range(60):                               # up to 30 s
        if ours(call(p, "/api/health")):
            return True
        time.sleep(0.5)
    return False


def read_geometry() -> dict:
    try:
        g = json.loads((config.state_dir() / GEOMETRY).read_text(encoding="utf-8"))
        return {k: int(g[k]) for k in ("width", "height", "x", "y")}
    except (OSError, ValueError, KeyError, TypeError):
        return {"width": 1200, "height": 860}


def save_geometry(w):
    if w.width and w.height:
        (config.state_dir() / GEOMETRY).write_text(json.dumps({"width": w.width, "height": w.height, "x": w.x, "y": w.y}), encoding="utf-8")


class SingleInstance:
    """One window at a time: a named mutex on Windows, a locked file elsewhere."""

    def __init__(self):
        self.handle = None
        if WINDOWS:
            k = ctypes.windll.kernel32
            self.handle = k.CreateMutexW(None, False, "Local\\dl-sub-dub-app")
            self.first = k.GetLastError() != 183            # ERROR_ALREADY_EXISTS
        else:
            import fcntl
            path = config.state_dir() / "app.lock"
            path.parent.mkdir(parents=True, exist_ok=True)
            self.handle = open(path, "w")
            try:
                fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.first = True
            except OSError:
                self.first = False

    @staticmethod
    def bring_to_front():
        if WINDOWS:
            u = ctypes.windll.user32
            h = u.FindWindowW(None, TITLE)
            if h:
                u.ShowWindow(h, 9)                      # SW_RESTORE
                u.SetForegroundWindow(h)


def open_window():
    single = SingleInstance()
    if not single.first:
        single.bring_to_front()
        return
    p = port()
    health = call(p, "/api/health")
    if health and not ours(health):
        return fail(i18n.t("app.port_taken", port=p))
    if not health and not start_service(p):
        return fail(i18n.t("app.not_started", port=p, log=config.state_dir() / LOG))
    url = f"http://127.0.0.1:{p}/"
    try:
        import webview
    except ImportError:
        print(i18n.t("app.no_window"))
        webbrowser.open(url)
        return
    if WINDOWS:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    webview.settings["ALLOW_DOWNLOADS"] = True
    w = webview.create_window(TITLE, url, min_size=(400, 500), **read_geometry())
    w.events.closing += lambda: save_geometry(w)
    call(p, "/api/window", {"open": True})
    storage = config.state_dir() / "window"
    try:
        webview.start(private_mode=False, storage_path=str(storage), icon=str(ICONS / ("dl-sub-dub.ico" if WINDOWS else "dl-sub-dub.png")))
    except Exception:                                   # no engine for the window (Linux without GTK nor Qt): the browser
        print(i18n.t("app.no_window"))
        webbrowser.open(url)
        return
    if (call(p, "/api/health") or {}).get("app"):     # started by an application: it stops once its work is done
        call(p, "/api/window", {"open": False})
    del single


def fail(message: str):
    print(message, file=sys.stderr)
    try:
        import webview
    except ImportError:
        return 1
    try:
        webview.create_window(TITLE, html=f"<p style='font:15px sans-serif;margin:2em'>{message}</p>", width=640, height=220)
        webview.start()
    except Exception:                                   # no engine for the window: the message on the terminal only
        pass
    return 1


# --- the shortcut ---
def command() -> list[str]:
    """What the shortcut runs: the Python of this installation, without a console on Windows."""
    python = Path(sys.executable)
    if WINDOWS and python.with_name("pythonw.exe").exists():
        python = python.with_name("pythonw.exe")
    return [str(python), "-m", "subtitles.app"]


def working_folder() -> Path:
    """The folder that holds the package: the installed copy, or the checkout when it runs from there."""
    return Path(__file__).resolve().parent.parent


def desktop_entry() -> str:
    """The .desktop file of Linux (freedesktop.org)."""
    exe = " ".join(f'"{a}"' if " " in a else a for a in command())
    return "\n".join(["[Desktop Entry]", "Type=Application", f"Name={TITLE}", f"Comment={i18n.t('app.description').split('.')[0]}",
                      f"Exec={exe}", f"Path={working_folder()}", f"Icon={ICONS / 'dl-sub-dub.png'}", "Terminal=false",
                      "Categories=AudioVideo;Video;", f"StartupWMClass={TITLE}", ""])


def shortcut_places(desktop: bool) -> list[Path]:
    if WINDOWS:
        from win32com.shell import shell, shellcon
        folders = [shellcon.CSIDL_PROGRAMS] + ([shellcon.CSIDL_DESKTOPDIRECTORY] if desktop else [])
        return [Path(shell.SHGetFolderPath(0, f, None, 0)) / f"{TITLE}.lnk" for f in folders]
    data = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    places = [data / "applications" / f"{TITLE}.desktop"]
    if desktop:
        found = shutil.which("xdg-user-dir") and subprocess.run(["xdg-user-dir", "DESKTOP"], capture_output=True, text=True).stdout.strip()
        places.append(Path(found or Path.home() / "Desktop") / f"{TITLE}.desktop")
    return places


def make_windows_shortcut(lnk: Path):
    import pythoncom
    from win32com.client import Dispatch
    from win32com.propsys import propsys, pscon
    from win32com.shell import shellcon

    exe, *args = command()
    s = Dispatch("WScript.Shell").CreateShortcut(str(lnk))
    s.TargetPath = exe
    s.Arguments = " ".join(args)
    s.WorkingDirectory = str(working_folder())
    s.IconLocation = str(ICONS / "dl-sub-dub.ico")
    s.Description = i18n.t("app.description").split(".")[0]
    s.Save()
    store = propsys.SHGetPropertyStoreFromParsingName(str(lnk), None, shellcon.GPS_READWRITE, propsys.IID_IPropertyStore)
    store.SetValue(pscon.PKEY_AppUserModel_ID, propsys.PROPVARIANTType(APP_ID, pythoncom.VT_LPWSTR))
    store.Commit()


def shortcut(what: str) -> int:
    if sys.platform == "darwin":
        print(i18n.t("app.no_shortcut_here", command=" ".join(command())))
        return 1
    if WINDOWS:
        try:
            import win32com  # noqa: F401
        except ImportError:
            print(i18n.t("app.need_pywin32"), file=sys.stderr)
            return 1
    places = shortcut_places(desktop=what in ("desktop", "remove"))
    if what == "remove":
        for place in places:
            if place.exists():
                place.unlink()
                print(i18n.t("app.shortcut_removed", path=place))
        return 0
    for place in places:
        place.parent.mkdir(parents=True, exist_ok=True)
        if WINDOWS:
            make_windows_shortcut(place)
        else:
            place.write_text(desktop_entry(), encoding="utf-8")
            place.chmod(0o755)
        print(i18n.t("app.shortcut_made", path=place))
    if WINDOWS:
        print(i18n.t("app.pin"))
    return 0


def main():
    try:
        i18n.set_language(config.load().get("ui_language"))
    except (config.ConfigError, OSError, ValueError):
        pass
    p = argparse.ArgumentParser(prog="python -m subtitles.app", description=i18n.t("app.description"))
    p.add_argument("--shortcut", nargs="?", const="menu", choices=["menu", "desktop", "remove"], help=i18n.t("app.shortcut"))
    a = p.parse_args()
    sys.exit(shortcut(a.shortcut) if a.shortcut else open_window())


if __name__ == "__main__":
    main()
