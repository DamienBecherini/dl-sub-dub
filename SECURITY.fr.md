# Sécurité

**Français** · [English](SECURITY.md)

dl-sub-dub tourne sur votre machine, pour vous seul. Sa page est servie par un petit service local ; il lit des vidéos,
appelle les moteurs que vous avez choisis, et écrit sous-titres et pistes doublées à côté de vos vidéos. Cette page
liste ce qui vous protège, vous, vos clés et votre machine, où cela se trouve dans le code, et ce qui n'est pas couvert.

## En un coup d'œil

| Risque | Ce qui l'empêche | Où |
|---|---|---|
| Un autre ordinateur atteint le service | Il n'écoute que sur `127.0.0.1` | [`web/server.py`](subtitles/web/server.py), `serve` |
| Un site que vous visitez pilote l'API (CSRF) | Les `POST`/`PUT` portant l'`Origin` d'un autre site sont refusés, comme toute requête que le navigateur dit faite par un autre site (`Sec-Fetch-Site`) | `Handler._foreign` |
| Un site atteint le service sous son propre nom (DNS rebinding) | Tout `Host` autre que `127.0.0.1` ou `localhost` sur le port est refusé | `Handler._foreign` |
| Un site affiche la page dans un cadre caché et guide vos clics (clickjacking) | `X-Frame-Options: DENY` et `frame-ancestors 'none'` sur chaque réponse | `Handler._security_headers` |
| Un site lit les réponses de l'API | Aucun en-tête CORS n'est envoyé : le navigateur les cache aux autres sites | `Handler._send` |
| Une clé d'API écrite en clair dans un fichier | Refusée : une clé est une référence, `env:NOM`, `file:/chemin` ou `sops:/fichier.env#NOM`, lue seulement à l'usage | [`config.py`](subtitles/config.py), `save`, `resolve_key` |
| Un fichier nommé `.mp4` qui est un programme | Les médias sont seulement lus (ffprobe, ffmpeg) ; **Ouvrir** ne lit que les extensions de vidéo et de son, par le lecteur du système | [`web/desktop.py`](subtitles/web/desktop.py), `open_path`, `PLAYABLE` |
| Une commande construite à partir d'un nom ou d'un titre | Chaque programme est lancé avec une liste d'arguments, jamais par un shell | partout où `subprocess` sert |
| Le site d'une vidéo donne un code de langue piégé (`../../x`) à l'une de ses pistes | Le code nomme un fichier et va dans des options de yt-dlp : seul un code simple (lettres, chiffres, tirets) est accepté, les autres pistes ne sont pas proposées | [`tracks.py`](subtitles/tracks.py), `is_language` |
| Un chemin qui sort du dossier de la page | Les fichiers statiques ne sont servis que depuis `web/static/` | [`web/routes/pages.py`](subtitles/web/routes/pages.py), `static_file` |
| Un script glissé malgré tout dans la page | Une politique de sécurité du contenu (CSP) stricte : seuls les fichiers de script du service s'exécutent (pas de code en ligne, pas d'eval, rien d'ailleurs), et la page ne parle qu'au service | `CSP` dans `web/server.py` |
| Un fichier glissé sur la page remplit le disque | La page dit où il sera copié et la place restante, et demande d'abord ; le service refuse une copie pour laquelle le disque n'a pas la place | [`web/routes/videos.py`](subtitles/web/routes/videos.py), `upload_room`, `upload_file` |
| Un fichier glissé sur la page en écrase un autre | Seul son nom est gardé (sans dossier), et il va dans le dossier de sortie ; un autre fichier du même nom reste, la copie prend « nom (2).ext » | [`web/routes/videos.py`](subtitles/web/routes/videos.py), `upload_file`, `free_name` |
| Un titre, une ligne de journal ou un sous-titre qui contient du HTML | La page écrit du texte, jamais du HTML : Preact l'échappe ; le seul HTML vient des catalogues de l'outil | [`web/static/v2/`](subtitles/web/static/v2/) |
| Une requête trop grosse | Les corps JSON de plus de 5 Mo sont refusés | [`web/router.py`](subtitles/web/router.py), `Request.json` |
| Un disque plein, un fichier coupé en deux | Place libre vérifiée avant un téléchargement et avant un MKV, avec un message clair | [`space.py`](subtitles/space.py) |
| Un yt-dlp ancien, ou remplacé à votre insu | Son âge est lu dans sa propre version (rien n'est demandé sur le réseau) ; la mise à jour ne se fait que sur votre clic, par `yt-dlp -U`, qui télécharge sa version officielle depuis ses publications GitHub et la vérifie ; jamais pendant un téléchargement ; installé avec pip ou un paquet, seule la commande est affichée | [`ytdlp.py`](subtitles/ytdlp.py) |
| Un service oublié en marche | Il s'arrête seul après une heure sans activité (réglage) et, démarré par l'application, une fois sa fenêtre fermée et son travail fini (`POST /api/window`, sous les mêmes protections que tout `POST`) | [`web/idle.py`](subtitles/web/idle.py) |

## Le réseau

- **Local seulement.** Le service écoute sur `127.0.0.1:4420`. Rien sur votre réseau ne peut l'ouvrir ; pour s'en
  servir depuis une autre machine il faudrait l'exposer vous-même, ce que l'outil ne fait pas et ne recommande pas.
- **Les requêtes venues de pages web.** Une page ouverte dans votre navigateur peut envoyer des requêtes à
  `127.0.0.1`. Quatre verrous l'en empêchent :
  - **Origin.** Un navigateur envoie toujours `Origin` avec un `POST` ou un `PUT`. Celle d'un autre site est refusée
    avant toute route. Les scripts et la ligne de commande n'en envoient pas et ne sont pas concernés.
  - **Sec-Fetch-Site.** Le navigateur dit lui-même qui a fait une requête. Celle d'un autre site, ou d'un autre port
    de cet ordinateur, est refusée, lecture comprise ; seule l'ouverture de la page depuis un lien reste possible.
  - **Host.** Un site peut faire pointer son propre nom vers `127.0.0.1` (DNS rebinding) pour paraître de même
    origine. Ses requêtes portent pourtant son nom dans `Host`, qui est refusé.
  - **Cadres.** La page refuse de s'afficher dans un autre site : aucun clic là-bas ne peut tomber sur un bouton ici.
- **Politique de sécurité du contenu.** Chaque réponse porte `default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data: https:`… La page n'a ni script en ligne ni gestionnaire en ligne (le thème et la taille sont posés par `static/boot.js`) : même un texte qui passerait l'échappement ne pourrait ni s'exécuter ni rien envoyer ailleurs. Les images peuvent venir de sites `https:` : les vignettes des adresses de vidéos.
- **Pas de lecture par d'autres sites.** Sans en-têtes CORS, le navigateur cache chaque réponse de l'API aux autres
  origines.

## Vos clés

- **Jamais en clair.** Les réglages refusent une clé en clair : un traducteur ou un serveur de transcription prend une
  *référence* (`env:ANTHROPIC_API_KEY`, `file:C:/clés/claude.txt`, `sops:D:/secrets/ia.env#CLE`). La clé est lue au
  moment d'un appel, et `sops` est lancé avec une liste d'arguments.
- **La page et l'API montrent** la référence, jamais la clé.
- **La procédure pour agent** ([AGENTS.md](AGENTS.md)) interdit à un agent IA d'écrire une clé dans un fichier ou dans
  la conversation, et fait demander avant tout ce qui exige un mot de passe, un compte ou un service payant.

## Fichiers et programmes

- **Les médias sont des données.** Vidéos et sons sont lus par ffprobe, ffmpeg et yt-dlp, jamais exécutés. Un
  programme renommé `.mp4` fait échouer l'étape avec un message « fichier illisible ».
- **Ouvrir** ne lit qu'un fichier dont l'extension est une vidéo ou un son (`.mp4 .mkv .webm .mov .avi .m4a .mp3
  .opus .ogg .wav .flac`), dans le lecteur par défaut : sous Windows, `os.startfile` choisit le programme d'après
  l'extension, un `.mp4` part donc dans le lecteur vidéo. Montrer un dossier passe par l'explorateur, le chemin en
  argument.
- **Pas de shell.** yt-dlp, ffmpeg, ffprobe, PowerShell (boîtes de dialogue), osascript, zenity, kdialog, sops, WSL
  et le moteur de doublage sont lancés avec des listes d'arguments. Le PowerShell des boîtes de dialogue lit le
  dossier de départ, son titre et son filtre dans son environnement, et l'AppleScript de macOS les reçoit en
  arguments : jamais collés dans du code (PowerShell termine aussi une chaîne entre guillemets à une apostrophe
  typographique, comme dans un dossier « d’été »).
- **Les adresses.** Seules les adresses `http://` et `https://` vont à yt-dlp, après ses options : une entrée ne peut
  pas devenir une option. L'adresse écrite dans un fichier vidéo (celle d'où il a été téléchargé) n'est contactée que
  si vous demandez les pistes de son site.
- **Ce que dit le site d'une vidéo est une donnée.** Son titre nomme le fichier : yt-dlp y remplace les caractères
  qu'un disque refuse, et l'outil le coupe à la longueur qu'un nom de fichier accepte. Le code de langue d'une piste
  est vérifié avant de nommer un fichier.
- **Vos fichiers restent les vôtres.** Le nettoyage ne touche que le dossier de travail `.work/` (audio extrait,
  copies, journaux et, si vous le demandez, les dossiers entiers des vieilles vidéos). Sous-titres, vidéos et MKV à
  côté de vos vidéos ne sont jamais supprimés. Un sous-titre retouché à la main n'est pas écrasé, sauf si vous
  demandez de refaire.

## Ce qui quitte votre machine

- **Vers le serveur de transcription réglé** : l'audio de chaque vidéo. Faites-le tourner chez vous (le serveur
  recommandé tourne sur votre ordinateur ou votre serveur domestique) ; sur le réseau local il circule comme vous
  l'avez réglé (`http://` sur un réseau local).
- **Vers le traducteur réglé** : le texte des sous-titres. Un modèle local garde tout chez vous ; une API (Claude ou
  une autre) reçoit le texte, en HTTPS, selon ses propres conditions. La relecture IA, quand vous l'activez, envoie ce
  texte au modèle choisi pour elle, et la question « quelle langue est parlée ? » d'une vidéo douteuse envoie environ
  1 500 caractères de sa transcription au modèle que vous y choisissez. Avec un modèle payant, un projet s'arrête avant
  de dépenser et affiche son estimation, quel que soit l'ordre du travail, sauf si vous avez demandé à ne plus être
  interrogé pour ce projet ; la ligne de commande ne demande pas (la commande vaut accord).
- **Vers les sites des vidéos** : ce que yt-dlp demande, comme quand vous les regardez ; des cookies seulement si vous
  avez ajouté vous-même `--cookies-from-browser`.
- **Rien d'autre** : ni télémétrie, ni compte, ni vérification de mise à jour.

## Vos droits sur les vidéos

Ne traitez que des vidéos que vous avez le droit d'utiliser, et ne clonez une voix (doublage) qu'avec ce droit aussi.
L'outil ne le vérifie pas à votre place.

## Ce dont il ne protège pas

- **Un programme qui tourne déjà sur votre machine** sous votre compte peut appeler l'API comme la page (sans
  `Origin`) : le service fait confiance à votre session, comme tout outil local.
- **Une faille de ffmpeg, de yt-dlp ou de votre lecteur vidéo** qu'un fichier piégé exploiterait. Gardez-les à jour
  (`yt-dlp -U`, votre gestionnaire de paquets, les mises à jour de VLC).
- **Les réglages eux-mêmes.** Une commande que vous écrivez dans les réglages (un serveur de transcription démarré à
  la demande, le Python du doublage, des options de yt-dlp) est lancée telle quelle : elle est la vôtre.
- **Le réseau local.** L'audio envoyé à un serveur de transcription en `http://` peut être lu sur ce réseau.
- **Un fichier de projet écrit par quelqu'un d'autre.** `dl-sub-dub.project.json` contient des chemins et des
  réglages, utilisés comme les vôtres : un chemin vers un partage réseau est contacté, par exemple. N'ouvrez que vos
  propres projets.
- **Un assistant IA branché par MCP** agit avec vos droits, comme la page. Les titres des vidéos qu'il lit viennent
  des sites : ce sont des données, pas des consignes, et c'est à l'assistant de les traiter ainsi.

## Signaler un problème

Ouvrez un ticket sur le dépôt, ou écrivez à l'auteur via [damien.becherini.fr](https://damien.becherini.fr) pour ce
que vous préférez ne pas rendre public tout de suite.
