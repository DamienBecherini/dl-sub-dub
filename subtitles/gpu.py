"""The graphics card shared by a local translation model and the dubbing.

A translation model served on this machine (llama.cpp, Ollama, LM Studio…) keeps its memory on the card after
translating; the dubbing (demucs, OmniVoice) then needs its own. Whether both fit depends on the machine: the
setting `[dubbing] gpu_sharing` says "together", "one_at_a_time" or "auto" (the result of the test if one was run,
otherwise one at a time: the cautious choice).

One at a time: before the dubbing phase of a project, the card is freed, in this order:
- `[dubbing] free_gpu`: addresses to call, `{"stop": {"url", "body"?}, "start": {"url", "body"?}}` (for instance
  the service that runs the model); the start is called again once the dubbing of the project is over;
- the `supervision` of the translator (the service that starts the local model when a translation needs it, see
  translation.OpenAI): its model is stopped, and the next translation starts it again by itself;
- Ollama: its model is unloaded (`keep_alive: 0`) and comes back by itself at the next request;
- otherwise the project waits, and the page asks to stop the translation model, then to continue.

The test (`run_test`) measures instead of guessing: it loads the translation model with a short exchange, then dubs a
few seconds while it stays loaded, and reads the memory of the card all along (nvidia-smi). Windows does not always
fail when the card is full: it overflows into the RAM and the dubbing becomes several times slower; so the test says
it fits only with a margin left (`MARGIN_MIB`).
"""
import json
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

from . import dubbing, i18n, source, translation

MODES = ("auto", "together", "one_at_a_time")
MARGIN_MIB = 512
OLLAMA_PORT = 11434
SAMPLE_S = 20
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}
SENTENCES = {"fr": "Ceci est un court essai pour mesurer la mémoire de la carte graphique.",
             "en": "This is a short test to measure the memory of the graphics card.",
             "es": "Esta es una breve prueba para medir la memoria de la tarjeta gráfica.",
             "de": "Dies ist ein kurzer Test, um den Speicher der Grafikkarte zu messen.",
             "it": "Questa è una breve prova per misurare la memoria della scheda grafica."}


# --- what to do ---
def sharing(cfg: dict) -> str:
    """"together" or "one_at_a_time", from the setting (auto: the test, otherwise one at a time)."""
    d = cfg.get("dubbing") or {}
    if d.get("device") == "cpu":
        return "together"                      # the dubbing on the processor leaves the card to the translation model
    mode = d.get("gpu_sharing") if d.get("gpu_sharing") in MODES else "auto"
    if mode != "auto":
        return mode
    test = d.get("gpu_test") or {}
    return "together" if test.get("fits") is True else "one_at_a_time"


def is_local(translator: dict | None) -> bool:
    """The translator is a model served on this machine (it may hold the card)."""
    if not translator or translator.get("type") in (None, "", "none", "claude"):
        return False
    host = urllib.parse.urlparse(str(translator.get("url") or "")).hostname or ""
    return host in LOCAL_HOSTS


def is_ollama(translator: dict) -> bool:
    return urllib.parse.urlparse(str(translator.get("url") or "")).port == OLLAMA_PORT


def how_to_free(cfg: dict, translator: dict | None) -> str:
    """"" (nothing to free), "hooks" (free_gpu), "supervision", "ollama" or "ask" (the user stops the model)."""
    if sharing(cfg) != "one_at_a_time" or not is_local(translator):
        return ""
    if ((cfg.get("dubbing") or {}).get("free_gpu") or {}).get("stop"):
        return "hooks"
    if translator.get("supervision"):
        return "supervision"
    return "ollama" if is_ollama(translator) else "ask"


def _post(call: dict, timeout: float = 30) -> None:
    url = call["url"]
    origin = "{0.scheme}://{0.netloc}".format(urllib.parse.urlparse(url))
    req = urllib.request.Request(url, data=json.dumps(call.get("body") or {}).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "Origin": origin})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        r.read()


def free(cfg: dict, translator: dict, log=print) -> bool:
    """Frees the card before the dubbing; False when the user has to do it (or it failed: the project waits)."""
    way = how_to_free(cfg, translator)
    try:
        if way == "hooks":
            _post(cfg["dubbing"]["free_gpu"]["stop"])
            time.sleep(3)                                   # the server lets go of its memory
            log(i18n.t("log.gpu_freed", name=translator.get("name") or translator.get("id")))
            return True
        if way == "supervision":
            _post({"url": str(translator["supervision"]).rstrip("/") + "/api/ai/stop"}, timeout=60)
            time.sleep(3)
            log(i18n.t("log.gpu_freed", name=translator.get("name") or translator.get("id")))
            return True
        if way == "ollama":
            base = str(translator["url"]).rstrip("/").removesuffix("/v1")
            _post({"url": base + "/api/generate", "body": {"model": translator.get("model"), "keep_alive": 0}})
            log(i18n.t("log.gpu_freed", name=translator.get("name") or translator.get("id")))
            return True
    except (OSError, ValueError, KeyError) as e:
        log(i18n.t("log.gpu_free_failed", error=e))
        return False
    return way == ""


def restore(cfg: dict, log=print) -> None:
    """Starts again what `free` stopped (free_gpu.start), once the dubbing of the project is over."""
    call = ((cfg.get("dubbing") or {}).get("free_gpu") or {}).get("start")
    if not call:
        return
    try:
        _post(call)
        log(i18n.t("log.gpu_restored"))
    except (OSError, ValueError) as e:
        log(i18n.t("log.gpu_free_failed", error=e))


# --- memory of the card ---
def read_memory() -> tuple[int, int] | None:
    """(used, total) MiB of the first NVIDIA card, or None (no nvidia-smi, another card)."""
    smi = shutil.which("nvidia-smi")
    if not smi:
        return None
    try:
        out = subprocess.run([smi, "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10).stdout
        used, total = (int(float(x)) for x in out.splitlines()[0].split(","))
        return used, total
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None


class Sampler:
    """Reads the memory of the card twice a second; keeps the highest."""

    def __init__(self):
        self.peak = 0
        self.total = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name="gpu-sampler")

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join(timeout=5)

    def _run(self):
        while not self._stop.is_set():
            m = read_memory()
            if m:
                self.peak, self.total = max(self.peak, m[0]), m[1]
            self._stop.wait(0.5)


# --- the test ---
def make_sample(folder: Path, video: str | None, language: str) -> tuple[Path, Path, Path]:
    """A sample to dub: SAMPLE_S seconds of a video of the project (a real voice), otherwise a sound made by ffmpeg;
    with a subtitle in its language and one in the dubbed language."""
    ffmpeg = source.tool("ffmpeg")
    clip = folder / "sample.m4a"
    if video and Path(video).is_file():
        start = max(0.0, (source.duration(Path(video)) or 0) / 2 - SAMPLE_S / 2)
        subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-ss", f"{start:.1f}", "-t", str(SAMPLE_S), "-i", str(video), "-vn",
                        "-c:a", "aac", str(clip)], check=True, capture_output=True)
    else:
        subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency=220:duration={SAMPLE_S}",
                        "-f", "lavfi", "-i", f"anoisesrc=d={SAMPLE_S}:a=0.05", "-filter_complex", "amix=inputs=2",
                        "-c:a", "aac", str(clip)], check=True, capture_output=True)
    src, dst = folder / "sample.src.srt", folder / f"sample.{language}.srt"
    cues = [(1, 9, "This is a short sample to check the memory of the graphics card."),
            (10, 18, "It is dubbed while the translation model stays loaded.")]
    text = SENTENCES.get(language, SENTENCES["en"])
    stamp = lambda s: time.strftime("%H:%M:%S", time.gmtime(s)) + ",000"           # noqa: E731
    src.write_text("\n".join(f"{i + 1}\n{stamp(a)} --> {stamp(b)}\n{t}\n" for i, (a, b, t) in enumerate(cues)), encoding="utf-8")
    dst.write_text("\n".join(f"{i + 1}\n{stamp(a)} --> {stamp(b)}\n{text}\n" for i, (a, b, _t) in enumerate(cues)), encoding="utf-8")
    return clip, src, dst


def run_test(cfg: dict, translator: dict | None, video: str | None = None, language: str = "fr", step=lambda name: None,
             log=print) -> dict:
    """Loads the translation model (if it is local), dubs a sample while it stays loaded, reads the memory all along.
    {fits, peak_mib, total_mib, model_mib, seconds, local, detail}; `fits` None when it could not be told."""
    if not dubbing.available(cfg):
        raise dubbing.DubbingError(i18n.t("err.dub_not_configured"))
    before = read_memory()
    result = {"local": is_local(translator), "translator": (translator or {}).get("id") or "", "date": time.strftime("%Y-%m-%d %H:%M"),
              "measured": before is not None}
    if result["local"]:
        step("model")
        try:
            translation.test(translator)
        except translation.TranslationError as e:
            raise translation.TranslationError(i18n.t("err.gpu_model_not_running", name=translator.get("name") or translator.get("id"), error=e)) from e
        loaded = read_memory()
        result["model_mib"] = loaded[0] if loaded else None
    work = Path(tempfile.mkdtemp(prefix="dsd-gpu-test-"))
    t0 = time.time()
    try:
        step("sample")
        clip, src, dst = make_sample(work, video, language)
        step("dubbing")
        with Sampler() as s:
            try:
                dubbing.dub(cfg, clip, work, src, dst, language, log, redo=True)
                error = ""
            except dubbing.DubbingError as e:
                error = str(e)
        result.update(seconds=round(time.time() - t0), peak_mib=s.peak or None, total_mib=s.total or (before[1] if before else None))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if error and (dubbing.is_out_of_memory(error) or error == i18n.t("err.dub_out_of_memory")):
        result.update(fits=False, detail=i18n.t("gpu.out_of_memory"))
    elif error:
        result.update(fits=None, detail=error)
    elif not result["measured"]:
        result.update(fits=None, detail=i18n.t("gpu.not_measured"))
    else:
        left = (result["total_mib"] or 0) - (result["peak_mib"] or 0)
        result.update(fits=left >= MARGIN_MIB, detail=i18n.t("gpu.left", left=left))
    return result
