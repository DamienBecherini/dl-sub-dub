"""The yt-dlp of the settings: its version, its age, how it was installed, and its update.

Video sites change often and an old yt-dlp fails on them: past OLD_DAYS the page says so and offers the update.
yt-dlp says in its verbose header how it was built ("variant": `win_exe`, `linux_exe`, `zip` for the single Unix
file…). Its own builds update themselves (`yt-dlp -U`, in the user's folder, no administrator rights): the page runs
it and shows its log. Installed with pip, pipx or Homebrew (`pip`), from the sources or by a system package, it says how
to update instead, and the page shows that command for the user to run (it may need rights the page does not have).
"""
import datetime as dt
import os
import re
import subprocess
import threading
import time

from . import config, i18n

OLD_DAYS = 60
CACHE_S = 3600                  # yt-dlp -v starts a process (through WSL: a second or two): asked again after an hour
TIMEOUT_S = 60
_HEADER = re.compile(r"yt-dlp version (?:(\w+)@)?(\d{4})\.(\d{2})\.(\d{2})[\w.]* .*?\((\w+)\)")


def command(cfg: dict | None = None) -> list[str]:
    """The configured command alone (without the download options): ["yt-dlp"], or a wrapper through WSL."""
    cfg = cfg if cfg is not None else config.load()
    return list((cfg.get("youtube") or {}).get("command") or ["yt-dlp"])


def self_updating(variant: str) -> bool:
    return variant == "zip" or variant.endswith("_exe")


def parse(text: str, now: dt.date | None = None) -> dict | None:
    """The verbose header of yt-dlp → {version, channel, variant, age_days, old, self_update}; None if not found."""
    m = _HEADER.search(text)
    if not m:
        return None
    channel, y, mo, d, variant = m.groups()
    date = dt.date(int(y), int(mo), int(d))
    age = ((now or dt.date.today()) - date).days
    return {"version": f"{y}.{mo}.{d}", "channel": channel or "stable", "variant": variant, "age_days": age,
            "old": age > OLD_DAYS, "self_update": self_updating(variant)}


def hint(variant: str, cmd: list[str]) -> str:
    """How to update a yt-dlp that does not update itself, in words with the command."""
    where = i18n.t("ytdlp.in_wsl") if cmd and cmd[0].lower() in ("wsl", "wsl.exe") else ""
    key = {"pip": "ytdlp.hint_pip", "source": "ytdlp.hint_source"}.get(variant, "ytdlp.hint_package")
    return where + i18n.t(key)


_cache: dict = {}
_lock = threading.Lock()


def info(fresh: bool = False, run=subprocess.run) -> dict:
    """{found, version, channel, variant, age_days, old, self_update, hint, command}; cached for an hour."""
    cmd = command()
    key = tuple(cmd)
    with _lock:
        hit = _cache.get(key)
        if hit and not fresh and time.time() - hit[0] < CACHE_S:
            return dict(hit[1])
    try:
        r = run([*cmd, "--ignore-config", "-v"], capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=TIMEOUT_S, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        found = parse((r.stderr or "") + (r.stdout or ""))
    except (OSError, subprocess.TimeoutExpired):
        found = None
    d = {"found": bool(found), "command": " ".join(cmd), **(found or {})}
    if found and not found["self_update"]:
        d["hint"] = hint(found["variant"], cmd)
    with _lock:
        _cache.clear()
        _cache[key] = (time.time(), d)
    return dict(d)


class Update:
    """`yt-dlp -U` run once in a thread, its lines kept for the page; the version read again at the end."""

    def __init__(self, cmd: list[str], popen=subprocess.Popen):
        self.cmd = [*cmd, "-U"]
        self.lines: list[str] = []
        self.state = "running"
        self.code: int | None = None
        self._popen = popen
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.thread.start()
        return self

    def _run(self):
        try:
            p = self._popen(self.cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                            errors="replace", env={**os.environ, "PYTHONIOENCODING": "utf-8"})
            for line in p.stdout:
                self.lines.append(line.rstrip("\n"))
            self.code = p.wait()
        except OSError as e:
            self.lines.append(str(e))
            self.code = -1
        self.state = "done" if self.code == 0 else "error"
        with _lock:
            _cache.clear()                          # the version changed

    def to_dict(self) -> dict:
        return {"state": self.state, "code": self.code, "lines": self.lines[-400:], "command": " ".join(self.cmd)}
