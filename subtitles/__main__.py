"""Subtitles (spoken language + target languages) for a video or playlist address, a video file or every video of a
folder, from the command line.

    python -m subtitles <address(es) | .txt file of addresses | video file | folder | .project.json file> [options]

Several addresses, or a text file with one address per line, are downloaded into one folder, processed as a project.
A folder becomes a project, as in the web interface: a dl-sub-dub.project.json file is written as the work goes, and
running the same command again after an interruption resumes where the project stopped.

Web interface: python -m subtitles.web (see README). Settings come from config.toml overridden by
config.json of the data folder (%LOCALAPPDATA%/subtitles unless moved in Settings; written by the web interface); the
options below take precedence.
"""
import argparse
import subprocess
import sys
import time
from pathlib import Path

from . import folder_cli, config, dubbing, i18n, local_transcription, pipeline, project_cli, source, steps, transcription, translation


def log(msg: str):
    print(time.strftime("[%H:%M:%S] ") + msg, flush=True)


def download_playlist(url: str, cfg: dict, output_dir: str | None, whole: bool):
    """Folder where the playlist was downloaded, to process as a project; None if the address is one video to process
    alone (not a playlist, or one video of a list without --playlist)."""
    command = source.ytdlp_command(cfg)
    info = source.playlist_info(url, command)
    if info is None:
        return None
    if info["with_video"] and not whole:
        log(i18n.t("log.cli_video_of_playlist", n=len(info["entries"])))
        return None
    folder = source.playlist_folder(source.download_dir(cfg, output_dir), info)
    log(i18n.t("log.cli_playlist", title=info["title"], n=len(info["entries"]), folder=folder))
    files, failures = source.download_playlist(info, command, folder, log)
    if failures:
        log(i18n.t("log.playlist_failures", n=len(failures)))
    if not files:
        raise source.SourceError(i18n.t("err.playlist_nothing", folder=folder))
    return folder


def download_list(urls: list[str], title: str, cfg: dict, output_dir: str | None):
    """Folder where the videos of a list of addresses were downloaded (each address: one video or a playlist)."""
    bad = source.not_urls(urls)
    if not urls or bad:
        raise source.SourceError(i18n.t("err.not_addresses", lines=", ".join(bad[:5]) or "-"))
    command = source.ytdlp_command(cfg)
    info = source.list_info(urls, command, source.folder_name(title))
    folder = source.playlist_folder(source.download_dir(cfg, output_dir), info)
    log(i18n.t("log.cli_list", n=len(urls), videos=len(info["entries"]), folder=folder))
    files, failures = source.download_playlist(info, command, folder, log)
    if failures:
        log(i18n.t("log.playlist_failures", n=len(failures)))
    if not files:
        raise source.SourceError(i18n.t("err.playlist_nothing", folder=folder))
    return folder


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):     # output redirected on Windows (cp1252): "→" must not stop the work
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--lang")
    chosen = pre.parse_known_args(argv)[0].lang
    cfg = config.load()
    i18n.set_language(chosen or cfg.get("ui_language"))
    p = argparse.ArgumentParser(prog="python -m subtitles", description=i18n.t("cli.description"), formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--lang", help=i18n.t("cli.lang", langs=", ".join(i18n.available())))
    p.add_argument("source", nargs="*", help=i18n.t("cli.source"))
    p.add_argument("--output-dir", help=i18n.t("cli.output"))
    p.add_argument("--language", help=i18n.t("cli.spoken"))
    p.add_argument("--languages", help=i18n.t("cli.targets"))
    p.add_argument("--no-cleanup", action="store_true", help=i18n.t("cli.no_cleanup"))
    p.add_argument("--transcription", help=i18n.t("cli.transcription"))
    p.add_argument("--translator", help=i18n.t("cli.translator"))
    p.add_argument("--redo", action="store_true", help=i18n.t("cli.redo"))
    p.add_argument("--list", action="store_true", help=i18n.t("cli.list"))
    p.add_argument("--until", choices=steps.STOPS[1:], help=i18n.t("cli.until"))
    p.add_argument("--new-project", action="store_true", help=i18n.t("cli.new_project"))
    p.add_argument("--no-subfolders", action="store_true", help=i18n.t("cli.no_subfolders"))
    p.add_argument("--playlist", action="store_true", help=i18n.t("cli.playlist"))
    p.add_argument("--stages", help=i18n.t("cli.stages"))
    p.add_argument("--project-dir", help=i18n.t("cli.project_dir"))
    p.add_argument("--name", help=i18n.t("cli.name"))
    p.add_argument("--no-mkv", action="store_true", help=i18n.t("cli.no_mkv"))
    p.add_argument("--order", choices=["steps", "videos"], help=i18n.t("cli.order"))
    a = p.parse_args(argv)

    if a.list:
        print(i18n.t("cli.servers", active=cfg.get("active_transcription")))
        for m in cfg.get("transcriptions", []):
            print(f"  {m['id']:10} {config.engine_name(m)}  {m.get('url', '')}")
        print(i18n.t("cli.translators", active=cfg.get("active_translator")))
        for m in cfg.get("translators", []):
            print(f"  {m['id']:10} {config.engine_name(m)}  {m.get('model', '')}")
        return 0
    if not a.source:
        p.error(i18n.t("cli.missing_source"))
    folder_options = dict(transcription=a.transcription, translator=a.translator, spoken_language=a.language,
                          languages=a.languages.split(",") if a.languages else None, output_dir=a.output_dir, redo=a.redo, stop=a.until,
                          new=a.new_project, recursive=not a.no_subfolders)
    try:
        if a.stages:                                    # the four steps, as on the page
            try:
                stages = project_cli.parse_stages(a.stages)
            except ValueError as e:
                p.error(str(e))
            return project_cli.run(a.source, stages, cfg, log, project_dir=a.project_dir, name=a.name, transcription=a.transcription,
                                   translator=a.translator, spoken_language=a.language,
                                   languages=a.languages.split(",") if a.languages else None, mkv=False if a.no_mkv else None,
                                   order=a.order)
        if len(a.source) > 1 or source.is_address_file(a.source[0]):     # a list of addresses: one project
            many = len(a.source) > 1
            urls = a.source if many else source.read_addresses(a.source[0])
            title = source.list_title() if many else Path(a.source[0]).stem
            return folder_cli.run(str(download_list(urls, title, cfg, a.output_dir)), cfg, log, **folder_options)
        one = a.source[0]
        if source.is_url(one):
            folder = download_playlist(one, cfg, a.output_dir, a.playlist)
            if folder is not None:
                return folder_cli.run(str(folder), cfg, log, **folder_options)
        elif folder_cli.is_folder_or_project(one):
            return folder_cli.run(one, cfg, log, **folder_options)
        r = pipeline.Request(one, cfg, a.transcription, a.translator, a.language, a.output_dir, a.redo,
                             languages=a.languages.split(",") if a.languages else None, cleanup=not a.no_cleanup)
        pipeline.run(r, log)
        return 0
    except (source.SourceError, transcription.TranscriptionError, translation.TranslationError, config.ConfigError,
            local_transcription.LocalServerError, dubbing.DubbingError, subprocess.SubprocessError) as e:
        log(i18n.t("log.error", error=e))
        return 1
    except KeyboardInterrupt:
        log(i18n.t("log.interrupted"))
        return 130


if __name__ == "__main__":
    sys.exit(main())
