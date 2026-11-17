# Avis sur les composants tiers

[English](THIRD_PARTY_NOTICES.md) · **Français**

dl-sub-dub est sous licence MIT ([LICENSE](LICENSE)) et son service n'utilise que la bibliothèque standard de Python. Il
embarque un seul fichier tiers, et il appelle des moteurs que vous installez vous-même : chacun a sa licence, et c'est
vous qui l'acceptez. Ce fichier les réunit pour les moteurs que la documentation recommande (lu en novembre 2026 :
vérifiez à la source avant de vous y fier).

## Embarqué avec l'outil

| Fichier | Quoi | Licence |
|---|---|---|
| `subtitles/web/static/vendor/preact-htm.js` | [htm](https://github.com/developit/htm) 3.1.1 avec [Preact](https://github.com/preactjs/preact) 10, en un seul module (détail dans le [README de ce dossier](subtitles/web/static/vendor/README.md)) | htm : Apache-2.0 ; Preact : MIT |

## Appelé, non embarqué

### Téléchargement et médias

| Moteur | Sert à | Licence |
|---|---|---|
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) | Télécharger les vidéos, les playlists, les pistes et les sous-titres d'un site | The Unlicense |
| [FFmpeg](https://ffmpeg.org) (`ffmpeg`, `ffprobe`) | Extraire l'audio, lire un fichier, fabriquer le MKV | LGPL 2.1 ou plus, ou GPL, selon la version que vous installez |

### Transcription (serveur recommandé)

| Moteur | Sert à | Licence |
|---|---|---|
| [transcription-handy-like](https://github.com/DamienBecherini/transcription-handy-like/blob/main/README.fr.md) | Le serveur de transcription | MIT (ses propres [avis](https://github.com/DamienBecherini/transcription-handy-like/blob/main/THIRD_PARTY_NOTICES.md) listent ce qu'il utilise) |
| Parakeet Ultra (`handy-computer/parakeet-ultra-gguf`) | Son modèle de reconnaissance de la parole | CC BY 4.0 |
| Silero VAD v6.2 (`handy-computer/silero-vad-v6.2-gguf`) | Sa détection de la voix | MIT |

### Traduction

| Moteur | Sert à | Licence |
|---|---|---|
| Un modèle local (llama.cpp, Ollama, LM Studio…) | Traduire sur votre machine | Celle du modèle que vous choisissez : lisez sa fiche. Les serveurs eux-mêmes : llama.cpp et Ollama sont sous MIT. |
| L'API Claude, avec le paquet facultatif `anthropic` (MIT) | Traduire, relire les noms propres | Les conditions d'utilisation d'Anthropic ; payant |
| Une autre API compatible OpenAI | Traduire | Les conditions du fournisseur |

### La fenêtre de l'application (facultative)

Installée par `pip install ".[app]"`, pour `python -m subtitles.app`.

| Paquet | Sert à | Licence |
|---|---|---|
| [pywebview](https://github.com/r0x0r/pywebview), avec bottle et proxy_tools | La fenêtre autour de la page | BSD-3-Clause ; bottle et proxy_tools : MIT |
| pythonnet, clr_loader, cffi, pycparser (Windows, avec pywebview) | Joindre le moteur de navigateur de Windows | MIT ; pycparser : BSD-3-Clause |
| pywin32 (Windows) | Le raccourci du menu Démarrer | PSF |
| Le moteur de navigateur du système : WebView2 (Windows), WebKit (macOS), GTK ou Qt (Linux) | Afficher la page | Fait partie du système, ou s'installe avec lui |

### Doublage (facultatif)

| Moteur | Sert à | Licence |
|---|---|---|
| [OmniVoice](https://github.com/k2-fsa/OmniVoice), le code (`omnivoice` sur PyPI) | Cloner la voix | Apache-2.0 |
| OmniVoice, le modèle pré-entraîné (`k2-fsa/OmniVoice`) | Cloner la voix | **CC BY-NC : usage non commercial seulement** (« à cause de contraintes de ses données d'entraînement », dit sa fiche) |
| Son tokenizer audio (Higgs Audio 2, dans le même modèle) | Coder et décoder le son | Boson Higgs Audio 2 Community License (fondée sur la licence communautaire Meta Llama 3 ; une licence étendue est demandée au-delà de 100 000 utilisateurs actifs par an) |
| [Demucs](https://github.com/adefossez/demucs) | Séparer la voix du fond sonore | MIT |
| WavLM pour la vérification du locuteur (`microsoft/wavlm-base-plus-sv`) | Distinguer les personnes qui parlent | Non indiquée sur sa fiche Hugging Face : voir le [dépôt de WavLM](https://github.com/microsoft/unilm/tree/master/wavlm) |
| PyTorch, torchaudio, Transformers, SoundFile | Faire tourner les modèles | Apache-2.0 et licences de type BSD (PyTorch en réunit plusieurs : voir son paquet) |
| num2words | Dire les nombres à voix haute | LGPL |

## Ce que cela veut dire en pratique

- Sous-titres seuls (téléchargement, transcription, traduction) : rien ci-dessus ne limite ce que vous faites de vos
  sous-titres, à part les conditions d'une API de traduction si vous en utilisez une.
- Doublage : le modèle de voix est réservé à un **usage non commercial**. Et quelle que soit la licence, ne clonez une voix
  qu'avec le droit d'utiliser cette voix et le contenu.
- Les vidéos : ne téléchargez et ne traitez que celles que vous avez le droit d'utiliser.
