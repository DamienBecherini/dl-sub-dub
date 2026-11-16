"""What the user selects (step 1, Select), turned into the items of a project.

An entry may be the address of a video, of a playlist (its videos, in their order), the path of a video file, of a
folder (its videos, subfolders included) or of a `.txt` file (one address per line). Each one gives zero, one or
several items; an entry that cannot be read gives an error, the others are kept.
"""
import os
import subprocess
from pathlib import Path

from .. import folder, i18n, source

ERRORS = (source.SourceError, OSError, subprocess.SubprocessError, ValueError)


def resolve(entries: list[str], command: list[str]) -> dict:
    """{items: [{source, title, duration, kind: "address"|"file", origin}], errors: [{entry, error}], title}.
    `origin`: the playlist, folder or list the item comes from ("" when given alone). `title`: a name for the
    project, that origin when there is only one."""
    items, errors, origins = [], [], []
    for entry in (e.strip().strip('"') for e in entries):
        if not entry or entry.startswith("#"):
            continue
        try:
            found, origin = _resolve_one(entry, command)
        except ERRORS as e:
            errors.append({"entry": entry, "error": str(e)})
            continue
        if not found:
            errors.append({"entry": entry, "error": i18n.t("err.nothing_found")})
        items += [{**f, "origin": origin} for f in found]
        if origin:
            origins.append(origin)
    names = list(dict.fromkeys(origins))
    return {"items": unique(items), "errors": errors, "title": names[0] if len(names) == 1 else ""}


def unique(items: list[dict]) -> list[dict]:
    """The same video twice is kept once (first place)."""
    seen, out = set(), []
    for i in items:
        key = i["source"].replace("\\", "/").lower() if i["kind"] == "file" else i["source"]
        if key not in seen:
            seen.add(key)
            out.append(i)
    return out


def _address(e: dict) -> dict:
    return {"source": e["url"], "title": e["title"] if e["title"] != e["url"] else "", "duration": e.get("duration"), "kind": "address"}


def _resolve_one(entry: str, command: list[str]) -> tuple[list[dict], str]:
    if source.is_url(entry):
        d = source.describe(entry, command)
        return [_address(e) for e in d["entries"]], d["playlist"] or ""
    p = Path(entry)
    if source.is_address_file(entry):
        urls = source.read_addresses(p)
        bad = source.not_urls(urls)
        if bad:
            raise source.SourceError(i18n.t("err.not_addresses", lines=", ".join(bad[:5])))
        return [_address(e) for e in source.list_info(urls, command, p.stem)["entries"]], p.stem
    if p.is_dir():
        return [{"source": v["path"], "title": v["name"], "duration": None, "kind": "file"} for v in folder.scan(p)], p.name
    if p.is_file():
        if p.suffix.lower() not in folder.VIDEO_EXTENSIONS | source.AUDIO_EXTENSIONS:
            raise source.SourceError(i18n.t("err.not_a_video", path=p.name))
        return [{"source": p.as_posix(), "title": p.name, "duration": None, "kind": "file"}], ""
    raise source.SourceError(i18n.t("err.file_not_found", path=entry))


def common_folder(paths: list[str]) -> Path | None:
    """Folder holding every file, if they share one (same drive)."""
    try:
        return Path(os.path.commonpath([str(Path(p).parent) for p in paths])) if paths else None
    except ValueError:
        return None


def default_location(items: list[dict], cfg: dict, output_dir: str | None = None) -> tuple[Path, bool]:
    """Where a new project lives by default, as (base folder, in a subfolder named after the project): with
    addresses, the download folder and a subfolder (the videos go there); with files only, the folder they share,
    otherwise the output folder and a subfolder. Nothing added yet: as for addresses (the usual start)."""
    if not items or any(source.is_url(i["source"]) for i in items):
        return source.download_dir(cfg, output_dir), True
    shared = common_folder([i["source"] for i in items])
    return (shared, False) if shared else (Path(output_dir or cfg["output_dir"]), True)


def project_folder(items: list[dict], title: str, cfg: dict, output_dir: str | None = None,
                   base: str | Path | None = None, named: bool | None = None) -> Path:
    """Folder of a new project: `base` (by default that of `default_location`), plus a subfolder named after the
    project if `named` (by default as `default_location` says)."""
    default_base, default_named = default_location(items, cfg, output_dir)
    base = Path(os.path.expanduser(str(base))) if base else default_base
    return base / source.folder_name(title) if (default_named if named is None else named) else base
