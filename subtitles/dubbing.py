"""AI dubbing: runs `dubbing_worker.py` in the separate environment that holds torch, demucs and OmniVoice.

`[dubbing]` table of the configuration: `enabled`, `command` (Python interpreter of that environment), `model`, `wsl`
(paths translated to /mnt/… when the command goes through WSL). The first line sent to the program is its job as
JSON; its input then stays open: closing it (cancellation, service shutdown) tells it to stop.
"""
import json
import re
import shutil
import subprocess
from collections import deque
from pathlib import Path

from . import cancellation, i18n, steps

WORKER = Path(__file__).with_name("dubbing_worker.py")
PREFIX = "» "                                   # lines of the program meant for the log


class DubbingError(Exception):
    pass


def is_out_of_memory(text: str) -> bool:
    """The graphics card was full (torch: "CUDA out of memory", OutOfMemoryError)."""
    t = text.lower()
    return "out of memory" in t or "outofmemoryerror" in t


def available(cfg: dict) -> bool:
    d = cfg.get("dubbing") or {}
    return bool(d.get("enabled") and d.get("command") and d.get("model"))


def path_for(cfg: dict, p: str | Path) -> str:
    """Path as seen by the program: D:/x becomes /mnt/d/x behind WSL."""
    text = str(p).replace("\\", "/")
    m = re.match(r"^([A-Za-z]):/(.*)$", text)
    return f"/mnt/{m.group(1).lower()}/{m.group(2)}" if m and (cfg.get("dubbing") or {}).get("wsl") else text


def worker_message(raw: str, progress=None, phase=None) -> str:
    """Line of the dubbing program: {"key": …, parameters} translated into the active language; text as it is otherwise.
    `progress(done, total)`: called with the sentences done, from its progress lines; `phase(name)`: called when it
    starts putting the voice and the background together ("dub_mix", the step the page then shows)."""
    try:
        d = json.loads(raw)
    except ValueError:
        return raw
    if not isinstance(d, dict) or "key" not in d:
        return raw
    if phase and d.get("key") == "log.dub_mixing":
        phase("dub_mix")
    if progress and d.get("key") in ("log.dub_progress", "log.dub_resumed"):       # sentences made, or taken again after a stop
        try:
            progress(int(d["done"]), int(d["n"]))
        except (KeyError, TypeError, ValueError):
            pass
    return i18n.t(d.pop("key"), **d)


SEPARATION = "separation"                     # dubbing/separation: the voice and the background of the video, shared by the languages
SENTENCES = "sentences-"                      # dubbing/sentences-<language>: the sentences made so far of a voice not finished


def clear_temporary(work: Path) -> int:
    """Once every voice asked is made: deletes the separated voice and background (about 1 GB for 50 minutes of video)
    and what the stopped dubbing of a language no longer asked left (`temp-<language>`, `sentences-<language>`).
    Returns the folders deleted."""
    folder = steps.dubbing_file(work, "x").parent
    gone = [d for d in (folder / SEPARATION, *folder.glob("temp-*"), *folder.glob(SENTENCES + "*")) if d.is_dir()]
    for d in gone:
        shutil.rmtree(d, ignore_errors=True)
    return len(gone)


def dub(cfg: dict, video: Path, work: Path, source_srt: Path, target_srt: Path, target: str, log=print, cancelled=lambda: False, redo: bool = False,
        progress=None, phase=None) -> Path:
    """Makes `dubbing/<target>.m4a` in the working folder (cloned voice mixed with the original background sound) and returns its path."""
    output = steps.dubbing_file(work, target)
    if output.is_file() and not redo:
        log(i18n.t("log.dub_already_done", lang=target, file=output.name))
        return output
    if not available(cfg):
        raise DubbingError(i18n.t("err.dub_not_configured"))
    output.parent.mkdir(parents=True, exist_ok=True)
    sentences = output.parent / f"{SENTENCES}{target}"
    if redo:
        shutil.rmtree(sentences, ignore_errors=True)       # made again: nothing of the voice before is taken
    conf = cfg["dubbing"]
    c = lambda p: path_for(cfg, p)  # noqa: E731
    job = {"video": c(video), "source_srt": c(source_srt), "target_srt": c(target_srt), "language": target, "output": c(output),
           "temp_dir": c(output.parent / f"temp-{target}"), "separation_dir": c(output.parent / SEPARATION), "sentences_dir": c(sentences), "model": conf["model"],
           "steps": int(conf.get("steps", 32)), "device": conf.get("device") or "auto"}
    try:
        p = subprocess.Popen([*conf["command"], c(WORKER)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0)
    except OSError as e:
        raise DubbingError(i18n.t("err.dub_program_not_found", error=e)) from e
    p.stdin.write((json.dumps(job, ensure_ascii=False) + "\n").encode("utf-8"))
    p.stdin.flush()
    tail, stopped = deque(maxlen=15), []

    def stop():
        """Stop or Cancel, told the moment it is asked, whatever the program is doing (it writes a line every 25
        sentences only, and nothing while it separates the voice, loads its model or mixes)."""
        stopped.append(True)
        try:
            p.stdin.close()                     # the program stops as soon as its input closes, and stops what it started
        except OSError:
            pass
        p.terminate()

    forget = cancellation.listen(cancelled, stop)
    buffer = b""
    while True:
        block = p.stdout.read(4096)
        if not block:
            break
        buffer += block
        *lines, buffer = re.split(rb"[\r\n]+", buffer)
        for raw in lines:
            line = raw.decode("utf-8", "replace")
            if line.startswith(PREFIX):
                log(worker_message(line[len(PREFIX):], progress, phase))
            elif line.strip() and line.strip(" ^~\t\r\n"):      # not Python's marks under the code (^^^^)
                tail.append(line)
    code = p.wait()
    forget()
    if stopped or cancelled():
        raise DubbingError(i18n.t("err.cancelled"))
    if code or not output.is_file():
        if is_out_of_memory(" ".join(tail)):
            raise DubbingError(i18n.t("err.dub_out_of_memory"))
        raise DubbingError(i18n.t("err.dub_failed", code=code, detail=" | ".join(list(tail)[-4:])[-500:]))
    return output
