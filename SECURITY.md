# Security

**English** · [Français](SECURITY.fr.md)

dl-sub-dub runs on your machine, for you alone. Its page is served by a small local service; it reads videos, calls the
engines you chose, and writes subtitles and dubbed tracks next to your videos. This page lists what protects you, your
keys and your machine, where it lives in the code, and what it does not cover.

## At a glance

| Risk | What stops it | Where |
|---|---|---|
| Another computer reaches the service | It listens on `127.0.0.1` only | [`web/server.py`](subtitles/web/server.py), `serve` |
| A website you visit drives the API (CSRF) | `POST`/`PUT` carrying the `Origin` of another site are refused; so is any request the browser marks as made by another site (`Sec-Fetch-Site`) | `Handler._foreign` |
| A website reaches the service through its own name (DNS rebinding) | Any `Host` other than `127.0.0.1` or `localhost` on the port is refused | `Handler._foreign` |
| A website shows the page in a hidden frame and steers your clicks (clickjacking) | `X-Frame-Options: DENY` and `frame-ancestors 'none'` on every answer | `Handler._security_headers` |
| A website reads the answers of the API | No CORS header is ever sent: the browser keeps them from other sites | `Handler._send` |
| An API key written in clear text in a file | Refused: a key is a reference, `env:NAME`, `file:/path` or `sops:/file.env#NAME`, read only when used | [`config.py`](subtitles/config.py), `save`, `resolve_key` |
| A file named `.mp4` that is a program | Media files are only read (ffprobe, ffmpeg); **Open** plays only video and sound extensions, through the system's player | [`web/desktop.py`](subtitles/web/desktop.py), `open_path`, `PLAYABLE` |
| A command built from a name or a title | Every program is started with a list of arguments, never through a shell | everywhere `subprocess` is used |
| The site of a video gives a trapped language code (`../../x`) to one of its tracks | The code names a file and goes into options of yt-dlp: only a plain code (letters, digits, hyphens) is accepted, the other tracks are not offered | [`tracks.py`](subtitles/tracks.py), `is_language` |
| A path that climbs out of the page's folder | Static files are served only from inside `web/static/` | [`web/routes/pages.py`](subtitles/web/routes/pages.py), `static_file` |
| A file dropped on the page overwrites another | Only its name is kept (no folder), and it goes into the output folder; a different file of that name stays, the copy takes "name (2).ext" | [`web/routes/videos.py`](subtitles/web/routes/videos.py), `upload_file`, `free_name` |
| A file dropped on the page fills the disk | The page says where it will be copied and how much room is left, and asks first; the service refuses a copy the disk has no room for | [`web/routes/videos.py`](subtitles/web/routes/videos.py), `upload_room`, `upload_file` |
| A title, a log line or a subtitle that holds HTML | The page writes text, never HTML: Preact escapes it; the only HTML comes from the tool's own catalogs | [`web/static/v2/`](subtitles/web/static/v2/) |
| A script slipped into the page anyway | A strict Content Security Policy: only the service's own script files run (no inline code, no eval, nothing from elsewhere), and the page talks only to the service | `CSP` in `web/server.py` |
| A request too large | JSON bodies over 5 MB are refused | [`web/router.py`](subtitles/web/router.py), `Request.json` |
| A full disk, a file cut in the middle | Free space checked before a download and before an MKV, with a clear message | [`space.py`](subtitles/space.py) |
| An old yt-dlp, or one replaced behind your back | Its age is read from its own version (nothing asked on the network); the update runs only on your click, with `yt-dlp -U`, which downloads its official build from its GitHub releases and checks it; never during a download; installed with pip or a package, only the command is shown | [`ytdlp.py`](subtitles/ytdlp.py) |
| A service left running | It stops by itself after an hour without activity (setting) | [`web/idle.py`](subtitles/web/idle.py) |

## The network

- **Local only.** The service binds `127.0.0.1:4420`. Nothing on your network can open it; to use it from another
  machine you would have to expose it yourself, which this tool does not do and does not recommend.
- **Requests from web pages.** A page you visit in your browser can send requests to `127.0.0.1`. Four locks stop it:
  - **Origin.** A browser always sends `Origin` with a `POST` or `PUT`. One from another site is refused before any
    route runs. Scripts and the command line send no `Origin` and are not concerned.
  - **Sec-Fetch-Site.** The browser itself says who made a request. One made by another site, or by another port of
    this computer, is refused, reading included; only opening the page from a link stays possible.
  - **Host.** A site can make its own name point to `127.0.0.1` (DNS rebinding) to look like the same origin. Its
    requests still carry its name in `Host`, which is refused.
  - **Frames.** The page refuses to be shown inside another site, so no click there can land on a button here.
- **Content Security Policy.** Every answer carries `default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data: https:`… The page has no inline script nor inline handler (the theme and the size are set by `static/boot.js`), so even text that would get past the escaping could not run, nor send anything elsewhere. Pictures may come from `https:` sites: the thumbnails of the video addresses.
- **No reading by other sites.** Without CORS headers, the browser hides every answer of the API from other origins.

## Your keys

- **Never in clear text.** The settings refuse a plain key: a translator or a transcription server takes a
  *reference* (`env:ANTHROPIC_API_KEY`, `file:C:/keys/claude.txt`, `sops:D:/secrets/ai.env#KEY`). The key is read
  at the moment of a call, and `sops` is started with a list of arguments.
- **What the page and the API show** is the reference, never the key.
- **The agent procedure** ([AGENTS.md](AGENTS.md)) forbids an AI agent to write a key in a file or in the
  conversation, and asks before anything that needs a password, an account or a paid service.

## Files and programs

- **Media are data.** Videos and sounds are read by ffprobe, ffmpeg and yt-dlp, never executed. A program renamed
  `.mp4` makes the step fail with a "file not readable" message.
- **Open** plays only a file whose extension is a video or a sound (`.mp4 .mkv .webm .mov .avi .m4a .mp3 .opus .ogg
  .wav .flac`), in the default player: on Windows, `os.startfile` picks the program from the extension, so an
  `.mp4` goes to the video player. Showing a folder uses the file manager with the path as an argument.
- **No shell.** yt-dlp, ffmpeg, ffprobe, PowerShell (dialog boxes), osascript, zenity, kdialog, sops, WSL and the
  dubbing worker are started with argument lists. The PowerShell of the dialog boxes reads the starting folder, its
  title and its filter from its environment, and the AppleScript of macOS gets them as arguments: never pasted into
  code (PowerShell also ends a quoted string at a typographic apostrophe, as in a folder « d’été »).
- **Addresses.** Only `http://` and `https://` addresses go to yt-dlp, after its options: an entry cannot become an
  option. The address written in a video file (the one it was downloaded from) is contacted only if you ask for the
  tracks of its site.
- **What the site of a video says is data.** Its title names the file: yt-dlp replaces the characters a disk
  refuses, and the tool cuts it to the length a file name accepts. The language code of a track is checked before it
  names a file.
- **Your files are left alone.** The cleanup touches only the working folder `.work/` (audio extracted, copies,
  logs, and, if you ask, whole folders of old videos). Subtitles, videos and MKVs next to your videos are never
  deleted. A subtitle file you edited by hand is not overwritten unless you ask to redo it.

## What leaves your machine

- **To the transcription server you set**: the audio of each video. Run it at home (the recommended server runs on
  your computer or your home server); over the local network it travels as you configured it (`http://` on a LAN).
- **To the translator you set**: the text of the subtitles. A local model keeps everything at home; an API (Claude or
  another) receives the text, over HTTPS, under its own terms. With a paid translator, a project done step by step stops
  before spending and shows its estimate (video by video, it does not: the page says so).
- **To the sites of the videos**: what yt-dlp asks, as when you watch them; cookies only if you added
  `--cookies-from-browser` yourself.
- **Nothing else**: no telemetry, no account, no update check.

## Your rights over the videos

Only process videos you have the right to use, and only clone a voice (dubbing) with that right too. The tool does
not check it for you.

## What it does not protect against

- **A program already running on your machine** under your account can call the API like the page does (no
  `Origin`): the service trusts your session, as any local tool does.
- **A flaw in ffmpeg, yt-dlp or your video player** that a crafted file would use. Keep them up to date
  (`yt-dlp -U`, your package manager, VLC's updates).
- **The settings themselves.** A command you write in the settings (a transcription server started on demand, the
  Python of the dubbing, options of yt-dlp) is run as written: it is yours.
- **The local network.** The audio sent to a transcription server over `http://` can be read on that network.
- **A project file written by someone else.** `dl-sub-dub.project.json` holds paths and settings, used as yours: a
  path to a network share is contacted, for instance. Open only your own projects.
- **An AI assistant connected through MCP** acts with your rights, as the page does. The titles of the videos it
  reads come from the sites: they are data, not instructions, and it is up to the assistant to treat them so.

## Reporting a problem

Open an issue on the repository, or write to the author through [damien.becherini.fr](https://damien.becherini.fr)
for anything you would rather not make public at once.
