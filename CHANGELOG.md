# Changelog

## 0.2.0 (2026-11-17)

An application of its own, Run that does what is ticked, and the project file in a new form (schema 2): a project
saved by this version is refused by 0.1.0, with a clear message.

- One dialog box at a time (File…, Folder…, List of addresses…, Open a project, the folders of the Settings): the
  clicks while one is open are ignored, and the service refuses a second one (409, from another tab or page) and
  brings the first to the front, instead of piling boxes up.
- Run says "Nothing to run" and waits when nothing ticked is left to do (every box unticked, or the steps ticked all
  done); it named the steps done and started and stopped at once.
- Run runs what is ticked: the steps of a project are the columns ticked for at least one video (each step for the
  videos ticked for it), and nothing else. Ticking the column of the dubbing (or one video) after the project was made
  without it now dubs with Run, once the subtitles are made; before, Run did only the steps chosen in a folded
  "Steps" block of the settings of the project, and a box ticked outside them was dashed and left aside. That block and
  the dashed boxes are gone. A new video is ticked for the steps of the project. The project file is schema 2: a
  project written before is read with its videos unticked for the steps it did not run. `POST
  /api/projects/<id>/stages` ticks those steps for every video ticked (with the steps they need: the download of an
  address to subtitle).
- dl-sub-dub as an application: `python -m subtitles.app` opens the page in a window of its own, with its icon, and
  starts the service without a console if it does not run; `--shortcut` creates the shortcut (Windows Start menu,
  Linux menu of the applications; `desktop` on the desktop too, `remove` removes them). Closing the window stops the
  service as soon as its work is done (`POST /api/window`); a service started otherwise is used and left running. A
  second start brings the window to the front. Optional: `pip install ".[app]"` (pywebview, and pywin32 on Windows);
  without it, the page opens in the browser.
- A line of the list that could not be read (the service stopped or restarting, a site that did not answer) has a
  Retry button: the line is read again and its videos take its place. On a phone, such a line shows its title, its
  message in full, then Retry (its title disappeared and its buttons fell on a row of their own).
- The cards of the compact list on a phone: the step boxes first, the action of the line after them. The action stays
  at the end of the row of boxes while it fits, and goes alone to the next row otherwise; it no longer takes a column
  of its own, which pushed the third box to a second row (at 360 px, the three boxes now fit on one row).

## 0.1.0 (2026-11-17)

First public version.

- One vocabulary: what the code, the API and the texts still called a "batch" is a project. The routes
  `/api/batches/…` are `/api/projects/…` (`GET /api/projects` lists them, `POST /api/projects` takes the settings of
  the project too and runs it with `start`, so a script still needs one request); `--new-batch` is `--new-project`;
  the Local service module says "video 3/22" where it said "batch 3/22". An old `subtitles.batch.json` is still read
  as a project. The 313 texts of the old page that no code named, and its style rules, are gone.
- Code, no change of use: a step of a video is a state machine (`Item.apply`, a table of events; an event heard too
  late, such as a Stop after a Cancel, changes nothing); what can be done now (the buttons of a line, what a step
  lacks, what Run takes) is decided once by the service (`project/rules.py`) and shown by the page, which worked it
  out again on its side; the flags slipped into option dictionaries are named fields, and the state of the graphics
  card is no longer written in the project file; the stream of a project to the page sleeps until the project changes
  instead of comparing its summary twice a second.
- Code, no change of use: the two longest files are cut by what each part does. `web/orchestrator.py` (1,300 lines)
  keeps the commands of a project, on `scheduler.py` (what starts and when), `fetching.py` (downloads, sites,
  tracks), `observed.py` (what the disk shows done), `card.py` (the graphics card), `last_project.py` and
  `project/choices.py`. `web/server.py` (1,300 lines) keeps the checks of each request; the routes are in
  `web/routes/`, one file per family of addresses. The style sheet of the page (700 lines) is five files by part of
  the page, written with the nesting of CSS; the page looks the same to the pixel.
- On a phone, the mark of a video's own settings stays on the row of its title, and the head of the compact list holds
  on one row (the steps as icons, a (?) that names them).
- A stopped dubbing takes its sentences again: each one is written as it is made, so a resume makes only those left
  (it started again from the first sentence: up to 13 minutes of graphics card for an episode).
- A French video with only French asked says "no other language to dub" on its line, as its mark did (it said "no
  translation"); what a step lacks has its full sentence in a bubble on the line too. The notice of addresses that
  cannot be subtitled before being downloaded counts those addresses only (it also counted videos waiting for their
  subtitles before a dubbing).
- Downloads: the title is cut in the name of the file to what a disk holds (180 bytes; shorter on Windows in a folder
  with a long path), where a long title, or one of 80 Japanese characters, failed with "File name too long"; a name
  still too long says so on the line. Stopping a project keeps the partial files of its downloads, and they resume
  where they stopped (they started again from zero). Adding the address a file of the project comes from says which
  file it is, instead of downloading a second copy.
- Several dubbed languages: while the first voice was put together with the background, the line said "about 14 s"
  (that mixing alone) with two voices still to dub, its ring fell back to almost empty, and the next voices kept the
  label of the mixing with no time left. The time left now counts the voices still to dub, the ring keeps its place,
  and the line says which voice it is on ("Dubbing (voice 2 of 3)").
- Dubbing: the voice separated from the background is made once per video and shared by the dubbed languages (it was
  made again for each language, about 2 minutes each on a graphics card, and after a stop when the languages had
  changed); it is deleted, with what a stopped language left, once every voice asked is made.
- The Run and Resume button names the steps with work left, as the state of each step of each video says (stopped
  during the dubbing, it said "Resume: Download + Subtitles + Dubbing" with the first two done).
- Stop during a dubbing takes effect at once: the request was only looked at when the dubbing program wrote its next
  line (every 25 sentences: up to half a minute, the fan still blowing), and a separation under way went on. The
  cancellation is now heard by listeners (`cancellation.py`): what waits for yt-dlp, the dubbing program or the
  transcription request is told the moment it is asked, instead of looking at a flag twice a second.
- The current tab of a line looks like a tab: its colour above it, a tinted background; the others answer to the pointer.
- The tabs of a line have the icon of their step before their name; on a phone only the icons, the current tab
  keeping its name (the five names wrapped on two rows).
- The log of a line follows the step that starts (unfolded during the subtitles, it kept showing them while the
  dubbing ran), and the step whose log is shown is marked.
- Cleanup: a stopped dubbing keeps only the separated voice, which a resume takes again; its other temporary files
  go when it is stopped (they stayed on the disk for good when it was never resumed: 1.5 GB for a 50-minute video).
  "Clean now" removes the separated voice too, and its sizes count these files.
- The languages of the subtitles and of the dubbed voices are locked while a video is being subtitled or dubbed (on
  the page, and refused by the service): that video kept the languages it started with, so a language added meanwhile
  left it done without it.
- Security: the language code of a track, given by the site of the video, is checked before it names a file or goes to
  yt-dlp (a code such as `../../x` would have written the track outside the folder); the partial files a project file
  names are deleted only in the folder of the project.
- Docs and help bubbles checked again: the English reference follows the French one, the dubbing no longer said to need
  an NVIDIA card, the WSL box of the dubbing explained as it works (the command itself starts with `wsl`), the original
  video said to go to the recycle bin (not deleted) when the MKV takes its place.
- Dubbing: where the voice models run is chosen (`[dubbing] device`: automatic, an NVIDIA card, an Apple chip, the
  processor alone), no longer CUDA only; measured on the processor: about 30 times slower. MKV tracks named in their
  own language, the dubbed voice saying what made it. A video whose file is gone says so on its line; adding a video
  already in the project says which line holds it. THIRD_PARTY_NOTICES in French.
- MKV: a picture kept as a video track of one frame (an MKV made by an earlier version, used as the source of a
  new dubbing) is a cover too and is no longer copied as a track: the added voices and subtitles went to the end of
  the file and played silent.
- THIRD_PARTY_NOTICES.md: the licences of the engines the tool calls. README: the languages each engine handles, how
  to uninstall, a phone screenshot. A path too long for Windows is said (ffmpeg wrote nothing and said nothing).
- The README rethought: what the tool does, what it needs, how to install it, how to connect the engines, how to use
  it, what is worth knowing, troubleshooting; the detail in `docs/reference.md`, the presentation merged into the
  README. AGENTS.md: an optional Dubbing step (the environment, the settings, the licences: the voice model of
  OmniVoice is under CC BY-NC).
- A video file whose address is known (yt-dlp writes it in the file; otherwise "Give its address…" in its ▾) has
  the Download tab too: what its site offers, read only when asked, and audio tracks or subtitles of the site added
  to the file without downloading the video again.
- An MCP server (`python -m subtitles.mcp_server`, standard library only) over the local API: an AI assistant
  (Claude Desktop, Claude Code, a voice assistant) makes projects, runs their steps or those of one video, follows them,
  and asks before a paid translation.
- Several dubbed languages: tick them or type their codes, for the project and for a video ("as the project" by
  default). One dubbed voice per language, all in the MKV, each named; a language not among the subtitles is added to
  them and translated first. A voice already made is kept: a language added later makes the MKV again with every voice
  (or adds the new one to a video the MKV replaced). The estimate counts one dubbing per language. A language chosen
  but not translated is said in the log, no longer replaced by another one.
  Ticking a dubbed language ticks it in the subtitles too (of the video, or of the project), so it is seen.
  Taking off the subtitles a language that is also dubbed asks first: take it off the dubbing too, keep the dubbing
  (its translation still made for the voice), or cancel.
- A video with settings of its own shows a small icon after its title (which ones in its bubble, a click opens their
  tab) instead of the words "own settings". An unticked line dims its content, not its ▾ menu (it was see-through
  and under the next lines).
- Each line has a ▾ next to its button: everything for this video alone (first the steps left in a row,
  Download + subtitle + dub or Subtitle + dub, each started when the one before ends; then download again with the quality and tracks
  of its tab, add tracks, subtitle again, dub or dub again, open the video, the dubbed MKV or the folder; what is not
  possible greyed with the reason). A language added to a video done makes its subtitles (and dubbing) to do again: its
  translation is made, the rest kept. Downloads write their address in the video (yt-dlp `--embed-metadata`): the
  Information tab shows it as Source.
- Compact list in columns: above, All, and for each step a box that ticks the step for every video (a dash when only
  some) and its icon; on each line the box of the step under it and the mark of its state under the icon (a ring,
  filling while it runs, orange when transcribed but not yet translated, an hourglass, a warning, a green ring checked
  once done). A video already transcribed shows Translate instead of Subtitle. An unfolded line always has the tabs of
  its steps, ticked or not; a video may choose its own final file (MKV or the dubbed voice alone) and whether its MKV
  replaces it (asked first). Choosing a setting of its own in the tab of a step ticks that step for the video (not
  back to the project's, nor a step done or running). A step ticked for a video ticks the steps it needs and are not
  done (the dubbing its subtitles, the subtitles of an address its download); unticked, it unticks the steps that
  need it; Run also runs the steps before that ticked videos still need: nothing ticked waits for a step that will not
  run. Help bubbles sized to their text (a short one was placed too far left).
- Compact list (the default) or Detailed, a switch above the list kept in the browser: in Compact, one row a video in columns: the icon
  of each step once above its column (its name in its bubble), on each line the box of the video, a box per step and
  beside it the mark of its state (percent while it runs, hourglass, warning, pause; its words in its bubble, a target
  of its own so a tap reads it without ticking), a green mark once done; the box of the header ticks every video.
  Detailed keeps the named boxes.
- Calmer page: the steps run alone are in a ▾ next to Run (Only download, Only subtitle, Only dub, with what is left
  to do) instead of three buttons above the list; adding videos folds into **+ Add videos** once the project has some
  (a file dragged over the page opens it); the count, the progress and one Tick all / Untick all button on one row; a
  line has its title alone on the first row and the kind, duration, boxes (left, in columns) and button under it, the x
  away from the button; nothing shown for "to do" (the box ticked says it), a step done a green mark instead of its box (Run skips it; Open plays the furthest result). The name of the project in the header is plain text (Modify
  renames it). Run on two lines (the verb, the steps smaller), so the bar holds on one row; the button of a line right
  after its boxes, away from the x. A step that cannot start yet shows an hourglass in its box (red when it cannot
  happen), its reason in a bubble (hover or tap); the ▾ menu counts them too: "Only dub (1 to do · 2 ⧗)".
- One page instead of the Batch and the Workshop: the list, with what the Batch had (where the project stands above the
  list, 1 of 2 finished and the time left). The running step of a line shows its percent and time left in its cell, with
  a thin bar. A video file has an **Information** tab (a picture made by ffmpeg, title, duration, size, quality, audio
  tracks and subtitles inside, subtitle files next to it; `GET /api/projects/{id}/items/{item}/media` and `/thumbnail`).
  A video can choose its own transcription server and translator (Subtitles tab of its line); a line with settings of
  its own is marked. The order of the work also next to Run; an Open button for the folder of the project. A paused
  translation resumes with the languages of its video (it took the project's).
- Translation: the chunks of a video can be sent several at once (translator field `chunks_at_once`, Settings
  "Chunks sent at once"); the first chunk of each wave keeps the lines translated before it as context, the others
  get the original lines before them. With a local llama.cpp started with `--parallel 4 --kv-unified`, measured on
  200 subtitles: 86-96 s one at a time, 60 s two at once, 50 s four at once.
- The spoken language, when it is not given and its common words do not tell it (another language than the seven
  known, a short or mixed text), is asked to the translator (a language model, which reads the subtitles anyway)
  from a sample, before translating: the subtitles of the spoken language get its code instead of `xx`, kept in
  `language.json` of the working folder.
- yt-dlp too old: past 60 days (or 14 when a download failed) the page says so and offers the update. yt-dlp's
  own builds (`.exe`, the single Unix file, in WSL too) update themselves: the service runs `yt-dlp -U` and the
  page shows its log live; installed with pip, pipx, Homebrew or a package, the command to run is shown. Never
  during a download. The Settings show the version and its age (`GET /api/ytdlp`, `POST /api/ytdlp/update`).
- Notifications: a bell in the header, off by default. The browser asks its permission only on a click on it, never
  again once refused (the bell then says how to allow it in the browser). When on, the end of a project, a video in
  error and a decision waiting (the cost of a paid translation) are notified while the page is not in sight. The
  bell can be hidden in the Settings (`web.notify_button`).
- Before Run: the time the steps ticked would take, from the speed measured on this machine (or announced in the
  settings) and the duration of each video, its detail per step in a bubble (downloads and videos of unknown duration
  not counted, and said so); the project summary gives the speeds (`rates`). The header stays on one line on wide
  screens (the name of the project gives way).
- Videos dropped on the page: a window says first that they will be copied (the browser does not give their path),
  where, their size and the room left on that disk, and offers to choose them in the file dialog instead (no copy),
  to paste their path, or to cancel. The service refuses a copy the disk has no room for (`GET /api/files/room`
  asks first; `PUT /api/files` answers 507).
- A strict Content Security Policy on every answer: only the service's own script files run (no inline code, no eval,
  nothing from elsewhere), the page talks only to the service, pictures from https: sites only (thumbnails). The
  pages hold no inline code any more (a test checks it).
- Themes: every palette of the Theme window can be changed (background, text, secondary text, accent; the other colours
  derived), and the user can make their own, listed with the light or dark palettes according to their background.
  While choosing, the contrasts are checked against WCAG (AAA, AA, large text only, not enough) with each pair and its
  ratio; saving is allowed anyway. A changed built-in palette has "Back to factory theme"; one's own theme can be
  deleted. Each palette shows its level. Kept in the browser and applied before the first paint (`static/boot.js`).
- Accessibility: two magnifiers (- and +) in the header of both pages make the whole page smaller or larger (80 to
  160 %, kept in the browser); on a touch screen or a phone width every target is at least 44 px, the boxes to tick
  larger, more space between a field and its buttons, the help bubbles with a 44 px touch area. The banner of the
  last project no longer overflows on a phone; the fold buttons have a name for screen readers. The theme and the
  size are set before the first paint by one script (`static/boot.js`) instead of inline code.

- SECURITY.md (and .fr): what protects the user, the keys and the machine, where, and what is not covered. Every answer
  forbids being framed by another site and type guessing (X-Frame-Options, frame-ancestors, nosniff, no referrer).
  Free space checked before a download (from its duration and height) and before an MKV, with a clear message.
- File... and List of addresses... take several files at once (Ctrl or Shift in the dialog box: Windows, zenity, kdialog,
  macOS); `POST /api/dialog` answers `paths` too.
- Settings: a **?** next to the fields explains them (the generation steps of the dubbing among them), on the page too;
  Cancel leaves the page; the bar Cancel / Save stays in sight under the header. A signature footer (author's site,
  repository). Cleanup: the dubbed voices as a category, the age of the logs in hours, days or months, limits of the
  whole working folder (`max_age_days`, `max_size_mb`: the oldest videos' folders go), the extracted audio removed when
  a video is cancelled or stopped too.
- The Batch shows the work in progress: the step of each video's task in words with its progress and time left
  (`work` of the project summary), the log of the video in progress unfolded, any row's log on a click. While a paused
  project finishes its videos, the page says so and offers Stop now; a project stopped stays paused (Resume), not new.
- Two modes on one project instead of the four step screens: the Batch (everything on one panel, then Run; a table of
  each video in each step once started) and the Workshop (one list, each video with its own boxes for the steps, its
  settings and its log). The first video read creates the project. The order of the work: step by step (the default)
  or video by video (`order` setting of the project, `--order videos` on the command line: each video dubbed right after its subtitles; no pause before the
  translation). A project made over finished videos reads "done", a new one "ready".
- The name and the folder of a project are chosen from the start (before anything is added, without waiting for the
  details of an address); once created, the project is renamed from its name in the header or Change in Select, and
  moved while nothing has started (`POST /api/projects/<id>/location`).
- While a project runs: Pause and Stop next to the run button (in its place), which reads Resume: … with its steps
  once paused; Cancel removed from the page (a cancelled project could not be run again; the API keeps it). The
  unfinished project offered in Select says the steps it resumes. A video ticked for download during a transcription
  starts at once (the downloads have a thread of their own), and the button of a step runs its videos left to do
  while the project runs (it was greyed).
- The page of the four steps is the page (`/`); the old page of batches and single videos is gone (its API stays).
  Settings have a page of their own (`/settings`), with the defaults of a new project and the dubbing; a project left
  unfinished is offered in Select, Open a project… opens one again (an old `subtitles.batch.json` too: it becomes a
  project, a finished one stays finished); every line has a Log tab (download, subtitles, dubbing).
- The project pushed to the page as soon as it changes (Server-Sent Events); the polling of a tab left open no longer
  keeps the service running.
- Command line: `--stages dl,sub,dub`, the four steps as on the page (`--project-dir`, `--name`, `--no-mkv`).
- `pip install .` ships the whole page (static/v2 and static/vendor were missing).
- docs/presentation.md: what the tool does, the loop, a project from start to end, what it measures, its choices and limits.
- Web interface and command line: one video, or a whole folder as a resumable batch.
- Transcription through any OpenAI-compatible server that returns word timestamps.
- Translation through a local LLM, the Claude API, any OpenAI-compatible API, or none.
- File pickers and "open folder" on Windows, macOS and Linux.
- Batches run several translations at once through a remote API, and show the time per video.
- Choose at the start: subtitles only, or subtitles with dubbing. Step-by-step batches, with a button to take one episode to the end first.
- AI review of proper names, validated by you before translation, and a glossary shared by the batch.
- Optional voice dubbing (off by default) and a multi-track MKV next to the video, which can replace the original once checked.
- Subtitle files edited by hand are no longer overwritten.
- Local JSON API documented in the README; the batch status shows the running video's actual step (transcription, translation…).
- English and French interface, task logs, error messages and command line; the page follows the browser language, a selector fixes it. Adding a language takes one file (`subtitles/locales/<code>.json`).
- Health endpoint `GET /api/health` following the "local service" contract v2: busy state, activity, remaining time, idle shutdown.
- Code, API, settings, command line options and data files in English: package `subtitles` (`python -m subtitles`), routes such as `/api/tasks` and `/api/batches`, `X-Language` header, `ui_language` setting, batch file `subtitles.batch.json`, working folders `.work/`. The French interface texts are unchanged.
- Web server: one function per route, declared with `@route("GET", "/api/tasks/{id}")`, and errors handled in one place. A malformed query (`?since=abc`) or an unreadable configuration now answers 400 instead of dropping the connection, and an answer sent before the request body is read is no longer lost on Windows.
- Security: the local API refuses requests from other websites (`Origin`) and DNS rebinding (`Host`). Before, a page open in the browser could make the service run a program or send a configured key to its own server.
- Playlists: the address of a playlist (YouTube playlist or channel, PeerTube channel…) is downloaded into a folder named after it, videos numbered in the order of the list, then processed as a batch like any folder. On the page, the preview offers **Download the playlist**; on the command line, a playlist address is enough (`--playlist` for the whole list from the address of one of its videos). Unavailable videos are skipped and reported; running it again resumes. A playlist address given as a single video is refused instead of downloading the whole list.
- Download settings (Settings > Downloads, `[youtube]` table): folder of the downloaded videos, maximum quality (1080p, sound up to 128 kb/s by default), merge into MP4, retries, other yt-dlp options. Video addresses tested with YouTube, Dailymotion, PeerTube and Arte.
- Batches: **Stop** pauses at once; the running video is interrupted within seconds and goes back to the list (what it finished is kept), instead of Cancel, which ends the batch. A transcription in progress is interrupted for real: the connection is closed, and transcription-handy-like stops between two chunks. A task stopped during translation is now cancelled, not in error.
- Theme window (🎨 in the header): mode (system, light, dark) and a palette per mode, five light and four dark (three without blue light), remembered in the browser. Phone header: title, theme, palettes and Settings on the first line, the title shortened if needed.
- A translation model on this machine and the dubbing share the graphics card: setting Graphics card (automatic from the check, together, or one AI at a time), Check the graphics card (loads the model, dubs a few seconds of a video of the project while it stays loaded, measures the memory: fits only with 512 MiB left at the peak, Windows overflowing into the RAM otherwise), and one at a time the card is freed before the dubbing: addresses of free_gpu (stop, then start once the dubbing is over), an Ollama model unloaded, or the project waits and the page asks to stop the model (Done, dub). A dubbing that runs out of memory says so on its line, with a button One AI at a time.
- Download screen: a download under way shows on its line a bar of one segment per part (video, each audio track, then the finishing) and the step in plain words: reading the site, pause asked by the site, video, audio, subtitles, merging, metadata, thumbnail, with the percentage, the size, the speed and the time left. The unfolded line has two tabs, Settings and Log: the log of yt-dlp as a terminal shows it (a progress line replaces the one before, the folder of the project written …/), opened first while the download runs or after an error, kept after the end until the service stops.
- Dubbing screen: the engine of the settings (or what is missing to use it), the dubbed language (the first one translated by default, or another one, for the project or for one video), an MKV with every track or the dubbed voice alone, the MKV in place of the original video; a line unfolds on the dubbed language of the video and what was made. The diagram says when the dubbing is not set up.
- The dubbing takes only the videos already subtitled and translated, and never starts their subtitles (a translation may be paid): the others say "subtitles first" or "no translation" instead of their button, and are not counted in "Dub the ticked videos". The steps a new project finds done on the disk (subtitles translated, MKV made) show as done. A dubbing with nothing translated is an error on its line with the reason, no longer "done". The location block of Select takes three compact lines.
- Buttons named after what they do: Download, Subtitle, Dub on a line, "Download the ticked videos (n to do)" under the list of a step, "Run: Download + Subtitles + Dubbing" (the steps ticked) at the bottom of the page. Open on a line done plays the video in the default player (the MKV on the Dubbing screen); only videos and sounds can be played this way.
- Select screen: where the project goes. A folder (by default the download folder with addresses, the folder the files share otherwise), typed or chosen with Browse…, a subfolder named after the project or not, and the resulting folder shown before the project is created.
- Subtitles screen: engines of transcription and translation, languages wanted, spoken language, pause before a paid translation (on by default for a paid translator), AI review of the names and where the files go, for the project; spoken language and languages for one video; the translation waiting for the go-ahead with its estimate per translator, and the names to check. A line unfolds with a click anywhere on it but its controls.
- Download screen: each address unfolds on what the site offers (read in the background: picture, author, date, heights, audio tracks original, dubbed or dubbed by AI, subtitles by hand or automatic), and takes its own quality, audio tracks (several in one file) and subtitles of the site (saved as `name.site.language.srt`). Once downloaded, a track can still be added without downloading the video again. The quality of the project is chosen at the top of the screen.
- Page of the four steps: each step has its own ticks (download four videos, subtitle two of them), each line its own buttons (Run, Retry, Translate when its translation waits, Cancel) for that video and that step only, and "Run this step" runs one step for the videos ticked on its screen.
- New page of the four steps, as a preview at `/v2` (link in the header): a diagram of the steps Select, Download, Subtitles, Dubbing (tick the steps to run, click one to see it), and the Select screen, where addresses, playlists, video files, folders and `.txt` lists are mixed in one list of videos to tick. "Run the ticked steps" creates the project and runs it; the other steps show where each video stands until they get their own screens.
  Each line added shows at once and gets its title within a fraction of a second (YouTube, Dailymotion, Vimeo), then its details (several lines read at once); an address that cannot be read shows its error on its line. Language, theme, palettes and Settings are in its header too.
- Batches are now projects (`dl-sub-dub.project.json`, next to the videos or in the output folder): each video is an item with its own steps (download, subtitles, dubbing), settings chosen per video on top of those of the project, and addresses downloaded two at a time while the videos already there are transcribed. Older `subtitles.batch.json` files are read and continued as projects.
- Cancelling a download stops it at once, not only between two videos: yt-dlp is killed (in WSL too) and its partial files are deleted. After a playlist or a list of addresses is stopped, the videos already downloaded open as a batch, and **Resume the download** fetches the rest into the same folder.
- Lists of addresses: several addresses (videos or playlists), one per line in the address field, in a `.txt` file (**List of addresses…**, or dropped on the page) or one after the other on the command line, are downloaded into one folder (named after the file, or `List <date>`), then processed as a batch.
- A cancelled single video can be resumed: **Resume** in the history and under its log, like **Retry** for a video in error (same settings, what is done is reused).
- Demo videos: Unsplash photos slowly zooming in behind the title (credits in `docs/demo/backgrounds/CREDITS.md`) instead of a plain gradient; screenshots taken again.
- Renamed **dl-sub-dub** (was auto-generate-subtitles): download, subtitle, dub, the first and last optional. Page and tab title, package name, repository addresses. The Python module (`subtitles`), the commands, the port and the settings folder do not change.
- Batch in pause: choosing Claude as translator shows its model and effort, saved in its settings, as before translation.
- A cancelled batch shows **Resume** too: it is read back from its file, finished videos kept, the others to do again.
- A batch resumed with another transcription engine moves the video still being transcribed to that engine, instead of letting it finish on the old one (Pause lets the running video go on).
- Cleanup: 0 hours of logs kept now deletes every log, even one just written (on Windows its date could be a few milliseconds ahead of the clock, so it was kept).
- Once a video or a batch is started, the settings of the page fold into a one-line summary; **Edit**, or giving a new video, folder or address, unfolds them.
- Quick start with an AI coding agent: a prompt to paste in the READMEs, and `AGENTS.md`, the install procedure the agent follows (hardware audit, recommended translator with an optional trial, settings through the API, test of each engine).
- Changing the interface language no longer reloads the page: the texts are translated in place, and what is being prepared (folder selection, languages per video, typed fields, open panels) stays as it is.
- Code: the task queue and the batch manager are separate classes (`web/tasks.py`, `web/batch_manager.py`), states are `StrEnum` (`subtitles/states.py`); the tests no longer write to the user folder.
