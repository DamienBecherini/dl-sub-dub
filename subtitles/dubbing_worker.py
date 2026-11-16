"""Program run by dubbing.py in the environment that holds torch, demucs, omnivoice, soundfile and num2words.

A whole video: separates the voice from the background sound, groups the translated subtitles into sentences,
generates each sentence by cloning the matching original passage (reference of 3 to 10 s, exact text taken from the
original subtitles), fits each sentence to its time, then mixes the voice with the background. A sentence shorter than
3 s is completed only with nearby lines spoken by the same voice (a speaker-verification model compares them): in a
dialogue, the next line is often another person's, whose voice would be cloned instead. Does not depend on the
subtitles package. Receives its job as JSON on the first line of its input; stops if the input closes.

Each sentence is written as soon as it is made (`sentences_dir` of the job, one file named after what the sentence is
made from): a dubbing stopped, cut by a failure or by the machine takes them again instead of generating them anew
(13 minutes of graphics card for an episode). A sentence whose translation, voice reference, model or number of steps
changed has another name: it alone is made again.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

for stream in (sys.stdin, sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):                   # (not when imported by the tests)
        stream.reconfigure(encoding="utf-8", errors="replace")

RATE = 24000
TIMES = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)")
END_OF_SENTENCE = re.compile(r"[.?!…]\s*$")
SENTENCE_GAP_S, SENTENCE_MAX_S = 0.8, 12
REF_MIN_S, REF_MAX_S = 3, 10
SPEAKER_MODEL = "microsoft/wavlm-base-plus-sv"          # x-vectors of speaker verification (transformers)
SAME_VOICE = 0.90                                        # cosine similarity above which two lines share a voice
NEAR_S = 90                                              # lines this far at most complete a reference


def sentence_key(text, start, end, language, model, steps, reference_ids, reference_text):
    """Names the audio of a sentence after everything it is made from: the same name, the same audio."""
    made_from = [text, round(start, 3), round(end, 3), language, model, steps, list(reference_ids), reference_text]
    return hashlib.sha1(json.dumps(made_from, ensure_ascii=False).encode("utf-8")).hexdigest()[:20]


def say(key, **params):
    """Progress for the log: a translation key and its parameters (see dubbing.worker_message)."""
    print("» " + json.dumps({"key": key, **params}, ensure_ascii=False), flush=True)


CHILDREN = []                                  # the programs started here and still running (demucs, ffmpeg)


def run(args):
    """Runs a program to its end (an error if it fails); kept in CHILDREN meanwhile, so that a stop ends it too."""
    p = subprocess.Popen(args)
    CHILDREN.append(p)
    try:
        code = p.wait()
    finally:
        CHILDREN.remove(p)
    if code:
        raise subprocess.CalledProcessError(code, args)


def ffmpeg(*args):
    run(["ffmpeg", "-y", "-loglevel", "error", *map(str, args)])


def read_srt(path):
    blocks = []
    for block in re.split(r"\r?\n\r?\n", Path(path).read_text(encoding="utf-8-sig").strip()):
        lines = block.splitlines()
        for i, line in enumerate(lines):
            m = TIMES.search(line)
            if m:
                v = list(map(int, m.groups()))
                blocks.append((v[0] * 3600 + v[1] * 60 + v[2] + v[3] / 1000, v[4] * 3600 + v[5] * 60 + v[6] + v[7] / 1000, " ".join(lines[i + 1:]).strip()))
                break
    return blocks


def sentences(cues):
    """Groups the subtitles into whole sentences, so as not to break the intonation at each line break."""
    groups, current = [], []
    for c in cues:
        if current and (c[0] - current[-1][1] > SENTENCE_GAP_S or c[1] - current[0][0] > SENTENCE_MAX_S):
            groups.append(current)
            current = []
        current.append(c)
        if END_OF_SENTENCE.search(c[2]):
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return [(g[0][0], g[-1][1], " ".join(x[2] for x in g)) for g in groups]


def numbers_to_words(text, language):
    from num2words import num2words

    def conv(m):
        raw = m.group(0)
        n = int(re.sub(r"\D", "", raw))
        try:
            return num2words(n, lang=language, to="year" if raw.isdigit() and 1100 <= n <= 2099 else "cardinal")
        except (NotImplementedError, ValueError, OverflowError):
            return raw
    return re.sub(r"\d(?:[\d   ]*\d)?", conv, text)


def reference_lines(cues, start, end, same_voice=None):
    """Indices of the original subtitles whose audio is the voice reference of the sentence [start, end]: its own
    lines (the longest one's voice: a sentence may hold a reply), then, while under REF_MIN_S, the nearest lines of
    the same voice (`same_voice(i, j)`), at most REF_MAX_S in all. Without `same_voice`, the own lines alone: never a
    line whose voice is not known to match."""
    own = [i for i, c in enumerate(cues) if c[1] > start and c[0] < end]
    if not own:
        own = [min(range(len(cues)), key=lambda i: abs(cues[i][0] - start))]
    length = lambda ids: sum(cues[i][1] - cues[i][0] for i in ids)  # noqa: E731
    main = max(own, key=lambda i: cues[i][1] - cues[i][0])
    chosen = [i for i in own if i == main or same_voice is None or same_voice(main, i)]
    if same_voice is not None and length(chosen) < REF_MIN_S:
        near = sorted((i for i in range(len(cues)) if i not in chosen and abs(cues[i][0] - start) <= NEAR_S),
                      key=lambda i: abs(cues[i][0] - start))
        for i in near:
            if length(chosen) >= REF_MIN_S:
                break
            if length(chosen) + cues[i][1] - cues[i][0] <= REF_MAX_S and same_voice(main, i):
                chosen.append(i)
    while length(chosen) > REF_MAX_S and len(chosen) > 1:
        chosen.remove(max(chosen, key=lambda i: abs(cues[i][0] - start)))     # the farthest first
    return sorted(chosen)


SPEAKER_MIN_S = 0.5            # the x-vector model needs some audio (0.1 s failed: "kernel size can't be greater…")


def speaker_bounds(total, a, b, rate=16000):
    """The samples of a subtitle's voice for its x-vector (start, end, silence to add): at least SPEAKER_MIN_S, widened
    around it within the audio (a subtitle at the very end, or past it, takes the last half second), padded with
    silence only if the whole audio is shorter."""
    need = int(SPEAKER_MIN_S * rate)
    start, end = min(max(0, int(a * rate)), total), min(total, int(b * rate))
    if end - start < need:
        middle = (start + max(start, end)) // 2
        start = max(0, min(middle - need // 2, total - need))
        end = min(total, start + need)
    return start, end, max(0, need - (end - start))


def pick_device(wanted: str = "auto") -> str:
    """Where the models run: the one asked for ("cuda", "mps", "cpu"), otherwise the best there is: an NVIDIA card
    (CUDA; an AMD card under Linux with PyTorch for ROCm answers as CUDA too), an Apple chip (MPS), the processor."""
    import torch
    if wanted in ("cuda", "mps", "cpu"):
        return wanted
    if torch.cuda.is_available():
        return "cuda"
    mps = getattr(torch.backends, "mps", None)
    return "mps" if mps is not None and mps.is_available() else "cpu"


def voice_vectors(voice, rate, cues, device="cuda"):
    """One x-vector (normalized) per original subtitle, from the separated voice; None if the model cannot be loaded
    (offline on a first run…): the references then keep their own lines only."""
    import numpy as np
    import torch
    import torchaudio
    try:
        from transformers import AutoFeatureExtractor, WavLMForXVector
        extractor = AutoFeatureExtractor.from_pretrained(SPEAKER_MODEL)
        model = WavLMForXVector.from_pretrained(SPEAKER_MODEL).to(device).eval()
    except Exception as e:                               # noqa: BLE001 (any failure: the safe references)
        print(f"speaker model unavailable: {type(e).__name__}: {str(e)[:200]}", flush=True)
        return None
    v16 = torchaudio.functional.resample(torch.tensor(voice, dtype=torch.float32), rate, 16000).numpy()
    out, size = [], None
    for a, b, _ in cues:
        start, end, pad = speaker_bounds(len(v16), a, b)
        piece = np.pad(v16[start:end], (0, pad))
        try:
            x = extractor(piece, sampling_rate=16000, return_tensors="pt").to(device)
            with torch.no_grad():
                e = torch.nn.functional.normalize(model(**x).embeddings[0], dim=-1).cpu().numpy()
            size = e.shape
        except RuntimeError as err:                      # a piece the model cannot read: like no other voice
            print(f"speaker vector skipped at {a:.1f} s: {str(err)[:120]}", flush=True)
            e = None
        out.append(e)
    if size is None:
        return None
    out = [e if e is not None else np.zeros(size, dtype=np.float32) for e in out]
    del model
    if device == "cuda":
        torch.cuda.empty_cache()
    return np.array(out)


def watch_input():
    """Cancellation or service shutdown: the input closes, the program exits without waiting for the current sentence,
    and ends the program it was waiting for (demucs would go on separating for minutes, on the graphics card)."""
    sys.stdin.read()
    for child in list(CHILDREN):
        try:
            child.kill()
        except OSError:
            pass
    os._exit(130)


def main():
    t = json.loads(sys.stdin.readline())
    threading.Thread(target=watch_input, daemon=True).start()

    import numpy as np
    import soundfile as sf
    import torch
    from omnivoice import OmniVoice

    def rms(x):
        return float(np.sqrt(np.mean(np.square(x)))) if len(x) else 0.0

    device = pick_device(t.get("device") or "auto")
    say({"cuda": "log.dub_device_cuda", "mps": "log.dub_device_mps", "cpu": "log.dub_device_cpu"}[device])
    tmp = Path(t["temp_dir"])
    tmp.mkdir(parents=True, exist_ok=True)
    (tmp / "refs").mkdir(exist_ok=True)
    job_start = time.time()
    duration = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", t["video"]],
                                    capture_output=True, text=True, check=True).stdout.strip())
    # The voice and the background of the video are the same for every language: separated once, kept apart from
    # the temporary folder of a language (which goes when its voice is made), and moved there only once whole, so
    # that a separation cut short is never taken for a finished one.
    made = tmp / "demucs/htdemucs/extract"
    shared = Path(t["separation_dir"]) if t.get("separation_dir") else made
    voice, background = shared / "vocals.wav", shared / "no_vocals.wav"
    if not (voice.exists() and background.exists()):     # already separated: a resume, or the next language, does not redo these minutes
        extract = tmp / "extract.wav"
        ffmpeg("-i", t["video"], "-vn", "-ac", 2, "-ar", 44100, extract)
        say("log.dub_separating")
        run([sys.executable, "-m", "demucs", *(["-d", "cpu"] if device == "cpu" else []), "--two-stems", "vocals",
             "-o", str(tmp / "demucs"), str(extract)])                          # demucs: CUDA if there is one, else the processor
        if device == "cuda":
            torch.cuda.empty_cache()
        if shared != made:
            shared.mkdir(parents=True, exist_ok=True)
            os.replace(made / "no_vocals.wav", background)
            os.replace(made / "vocals.wav", voice)
            extract.unlink(missing_ok=True)
    else:
        say("log.dub_separated")
    v, rate_v = sf.read(voice)
    v = v.mean(axis=1)

    original = read_srt(t["source_srt"])
    groups = sentences(read_srt(t["target_srt"]))
    if not original or not groups:
        raise SystemExit("empty subtitles")
    say("log.dub_sentences", n=len(groups))
    vectors = voice_vectors(v, rate_v, original, device=device)
    same_voice = None if vectors is None else (lambda i, j: float(vectors[i] @ vectors[j]) >= SAME_VOICE)
    gap = np.zeros(int(0.15 * rate_v))
    # half precision on a card only: the processor and the Apple chip compute in full precision
    model = OmniVoice.from_pretrained(t["model"], device_map="cuda:0" if device == "cuda" else device,
                                      dtype=torch.float16 if device == "cuda" else torch.float32)
    track = np.zeros(int(duration * RATE) + RATE)

    def place(a, audio):
        start = int(a * RATE)
        track[start:start + len(audio)] += audio[:len(track) - start]

    # What each sentence is made from, then the sentences a dubbing stopped before this one already made: taken again.
    kept = Path(t["sentences_dir"]) if t.get("sentences_dir") else None
    if kept:
        kept.mkdir(parents=True, exist_ok=True)
    todo, done = [], 0
    for a, b, text in groups:
        lines = reference_lines(original, a, b, same_voice)
        ref_text = " ".join(original[i][2] for i in lines)
        file = kept / (sentence_key(text, a, b, t["language"], t["model"], t["steps"], lines, ref_text) + ".wav") if kept else None
        try:
            audio = sf.read(str(file), dtype="float32")[0] if file is not None and file.is_file() else None
        except Exception as e:                           # noqa: BLE001 (a file cut short: that sentence is made again)
            print(f"sentence not read, made again: {type(e).__name__}: {str(e)[:120]}", flush=True)
            audio = None
        if audio is None:
            todo.append((a, b, text, lines, ref_text, file))
        else:
            place(a, audio)
            done += 1
    if done:
        say("log.dub_resumed", done=done, n=len(groups))
    for made, (a, b, text, lines, ref_text, file) in enumerate(todo, 1):
        parts = [v[int(original[i][0] * rate_v):int(original[i][1] * rate_v)] for i in lines]
        piece = np.concatenate([x for p in parts for x in (p, gap)][:-1])
        ref = tmp / "refs" / "sentence.wav"
        sf.write(ref, piece, rate_v)
        audio = model.generate(text=numbers_to_words(text, t["language"]), ref_audio=str(ref), ref_text=ref_text,
                               duration=b - a, num_step=t["steps"], language=t["language"])[0]
        gain = min(3.0, max(0.3, rms(piece) / max(rms(audio), 1e-6)))
        audio = np.clip(audio * gain, -1, 1)
        place(a, audio)
        if file is not None:                             # whole or not at all: written under another name, then renamed
            part = file.with_suffix(".part")
            sf.write(str(part), audio, RATE, format="WAV", subtype="PCM_16")
            os.replace(part, file)
        if made % 25 == 0 or made == len(todo):
            say("log.dub_progress", done=done + made, n=len(groups), s=round(time.time() - job_start))

    say("log.dub_mixing")                     # writing the voice, then mixing it with the background: a minute or more
    pv = tmp / "voice.wav"
    sf.write(pv, track, RATE)
    partial = tmp / "mix.m4a"
    ffmpeg("-i", pv, "-i", background, "-filter_complex", "[0:a]aresample=44100,aformat=channel_layouts=stereo[v];[v][1:a]amix=inputs=2:normalize=0,alimiter=limit=0.95[m]",
           "-map", "[m]", "-t", duration, "-c:a", "aac", "-b:a", "192k", partial)
    os.replace(partial, t["output"])
    shutil.rmtree(tmp, ignore_errors=True)
    if kept:
        shutil.rmtree(kept, ignore_errors=True)          # the voice is made: its sentences are no longer needed
    say("log.dub_done", s=round(time.time() - job_start))


if __name__ == "__main__":
    main()
