"""Where a download stands, from the lines of yt-dlp (taken from a real download, paths shortened)."""
import sys

from subtitles import source
from subtitles.web.dlprogress import DownloadProgress

LINES = """[youtube] Extracting URL: https://www.youtube.com/watch?v=woqBQG7LG8s
[youtube] woqBQG7LG8s: Downloading webpage
[info] woqBQG7LG8s: Downloading 1 format(s): 399+251
[info] Writing video subtitles to: /mnt/d/v/001 - Lesson.site.en.vtt
[download] Destination: /mnt/d/v/001 - Lesson.site.en.vtt
[download]  100% of   12.00KiB in 00:00:00 at 1.00MiB/s
[info] Writing video thumbnail 45 to: /mnt/d/v/001 - Lesson.webp
[download] Destination: /mnt/d/v/001 - Lesson.f399.mp4
[download]   0.1% of    1.02MiB at  301.66KiB/s ETA 00:03
[download]  42.0% of    1.02MiB at    5.99MiB/s ETA 00:01""".split("\n")
REST = """[download] 100% of    1.02MiB in 00:00:00 at 6.00MiB/s
[download] Destination: /mnt/d/v/001 - Lesson.f251.webm
[download]  50.0% of  755.16KiB at  9.14MiB/s ETA 00:00
[download] 100% of  755.16KiB in 00:00:00 at 9.14MiB/s
[Merger] Merging formats into "/mnt/d/v/001 - Lesson.mp4"
Deleting original file /mnt/d/v/001 - Lesson.f399.mp4
[Metadata] Adding metadata to "/mnt/d/v/001 - Lesson.mp4"
[EmbedThumbnail] mutagen: Adding thumbnail to "/mnt/d/v/001 - Lesson.mp4\"""".split("\n")


def test_the_parts_and_the_step_under_way():
    p = DownloadProgress()
    p.feed("[info] woqBQG7LG8s: Downloading 1 format(s): 399+251")
    assert [x["kind"] for x in p.to_dict()["parts"]] == ["video", "audio"]
    for line in LINES[3:7]:
        p.feed(line)
    d = p.to_dict()
    assert d["phase"] == "subtitles" and d["language"] == "en" and d["percent"] is None
    for line in LINES[7:]:
        p.feed(line)
    d = p.to_dict()
    assert d["phase"] == "video" and d["percent"] == 42.0 and d["speed"] == "5.99MiB/s" and d["eta"] == "00:01" and d["part"] == 1
    for line in REST[:3]:
        p.feed(line)
    d = p.to_dict()
    assert d["phase"] == "audio" and d["part"] == 2 and d["parts"][0] == {"kind": "video", "percent": 100.0, "done": True}
    for line in REST[3:]:
        p.feed(line)
    d = p.to_dict()
    assert d["finishing"] and d["phase"] == "thumbnail" and all(x["done"] for x in d["parts"])


def test_one_format_and_no_formats_line():
    p = DownloadProgress()
    p.feed("[info] x: Downloading 1 format(s): 18")
    p.feed("[download] Destination: /v/a.mp4")
    p.feed("[download]  10.0% of 5.00MiB at 1.00MiB/s ETA 00:04")
    assert p.to_dict()["phase"] == "both" and p.to_dict()["percent"] == 10.0
    q = DownloadProgress()                                     # an older yt-dlp, another site: parts as they come
    q.feed("[download] Destination: /v/a.mp4")
    q.feed("[download]  10.0% of ~ 5.00MiB at 1.00MiB/s ETA 00:04")
    assert q.to_dict()["parts"] == [{"kind": "both", "percent": 10.0, "done": False}]


def test_the_log_is_what_a_terminal_shows():
    p = DownloadProgress()
    p.note("Downloading the video")
    for line in LINES + REST:
        p.feed(line)
    lines = [x[11:] for x in p.lines()]                       # without the time
    assert lines[0] == "Downloading the video"
    assert lines.count("[download] 100% of    1.02MiB in 00:00:00 at 6.00MiB/s") == 1
    assert not any("0.1% of" in x or "42.0% of" in x for x in lines)   # each replaced by the next progress line
    assert lines[-1].startswith("[EmbedThumbnail]")


def test_the_lines_of_a_command_come_one_by_one():
    seen = []
    code = source._run_cancellable([sys.executable, "-c", "print('one'); print('été')"], lambda: False, seen.append)
    assert code == 0 and [s.strip() for s in seen] == ["one", "été"]


def test_reading_the_site_then_the_pause_it_asks():
    p = DownloadProgress()
    p.feed("[youtube] woqBQG7LG8s: Downloading webpage")
    assert p.to_dict()["phase"] == "reading"
    p.feed("[download] Sleeping 5.00 seconds as required by the site...")
    assert p.to_dict()["phase"] == "waiting"
    p.feed("[download] Destination: /v/a.f399.mp4")
    assert p.to_dict()["phase"] == "both"


def test_the_folder_of_the_project_is_short_in_the_log():
    p = DownloadProgress("D:/Vidéos/Cours")
    p.feed('[Merger] Merging formats into "/mnt/d/Vidéos/Cours/001 - Lesson.mp4"')
    p.note(r"Downloading to D:\Vidéos\Cours\001 - Lesson.mp4")
    assert [x[11:] for x in p.lines()] == ['[Merger] Merging formats into "…/001 - Lesson.mp4"', "Downloading to …/001 - Lesson.mp4"]
