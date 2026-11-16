# Third-party notices

**English** · [Français](THIRD_PARTY_NOTICES.fr.md)

dl-sub-dub itself is under the MIT licence ([LICENSE](LICENSE)) and its service uses the Python standard library only.
It ships one third-party file, and it calls engines that you install yourself: each has its own licence, and you are
the one who accepts it. This file gathers them for the engines the documentation recommends (read in November 2026:
check the source before relying on it).

## Shipped with the tool

| File | What | Licence |
|---|---|---|
| `subtitles/web/static/vendor/preact-htm.js` | [htm](https://github.com/developit/htm) 3.1.1 with [Preact](https://github.com/preactjs/preact) 10, as one module (details in the [README of that folder](subtitles/web/static/vendor/README.md)) | htm: Apache-2.0; Preact: MIT |

## Called, not shipped

### Download and media

| Engine | Used for | Licence |
|---|---|---|
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) | Downloading videos, playlists, tracks and subtitles of a site | The Unlicense |
| [FFmpeg](https://ffmpeg.org) (`ffmpeg`, `ffprobe`) | Extracting the audio, reading a file, making the MKV | LGPL 2.1 or later, or GPL, depending on the build you install |

### Transcription (recommended server)

| Engine | Used for | Licence |
|---|---|---|
| [transcription-handy-like](https://github.com/DamienBecherini/transcription-handy-like) | The transcription server | MIT (its own [notices](https://github.com/DamienBecherini/transcription-handy-like/blob/main/THIRD_PARTY_NOTICES.md) list what it uses) |
| Parakeet Ultra (`handy-computer/parakeet-ultra-gguf`) | Its speech model | CC BY 4.0 |
| Silero VAD v6.2 (`handy-computer/silero-vad-v6.2-gguf`) | Its voice detection | MIT |

### Translation

| Engine | Used for | Licence |
|---|---|---|
| A local model (llama.cpp, Ollama, LM Studio…) | Translating on your machine | That of the model you choose: read its card. The servers themselves: llama.cpp and Ollama are under MIT. |
| The Claude API, with the optional `anthropic` package (MIT) | Translating, reviewing the proper names | Anthropic's terms of service; paid |
| Another OpenAI-compatible API | Translating | The provider's terms |

### Dubbing (optional)

| Engine | Used for | Licence |
|---|---|---|
| [OmniVoice](https://github.com/k2-fsa/OmniVoice), the code (`omnivoice` on PyPI) | Cloning the voice | Apache-2.0 |
| OmniVoice, the pre-trained model (`k2-fsa/OmniVoice`) | Cloning the voice | **CC BY-NC: non-commercial use only** ("due to constraints from its training data", says its card) |
| Its audio tokenizer (Higgs Audio 2, in the same model) | Coding and decoding the audio | Boson Higgs Audio 2 Community License (based on the Meta Llama 3 Community License; an expanded licence is asked above 100,000 annual active users) |
| [Demucs](https://github.com/adefossez/demucs) | Separating the voice from the background | MIT |
| WavLM for speaker verification (`microsoft/wavlm-base-plus-sv`) | Telling the speakers apart | Not stated on its Hugging Face card: see the [WavLM repository](https://github.com/microsoft/unilm/tree/master/wavlm) |
| PyTorch, torchaudio, Transformers, SoundFile | Running the models | Apache-2.0 and BSD-style licences (PyTorch bundles several: see its package) |
| num2words | Reading numbers aloud | LGPL |

## What it means in practice

- Subtitles only (download, transcription, translation): nothing above restricts what you do with your subtitles, apart
  from the terms of a translation API if you use one.
- Dubbing: the voice model is for **non-commercial use**. And whatever the licence, clone a voice only with the right
  to use that voice and the content.
- The videos: download and process only those you have the right to use.
