"""Free space checked before a download and before an MKV; the security headers of every answer."""
from collections import namedtuple
from pathlib import Path

import pytest

from subtitles import space
from subtitles.web import downloads

Usage = namedtuple("Usage", "total used free")
GB = 2**30


def test_enough_or_not(tmp_path):
    space.check(tmp_path, 1 * GB, usage=lambda p: Usage(0, 0, 3 * GB))
    with pytest.raises(space.NotEnoughSpace, match="1.4"):
        space.check(tmp_path / "not" / "made", 1 * GB, usage=lambda p: Usage(0, 0, int(1.4 * GB)))
    space.check(tmp_path, 10 * GB, usage=lambda p: (_ for _ in ()).throw(OSError("network drive")))   # unknown: goes on


def test_the_size_of_a_download():
    assert space.download_need(None, 1080) == 0
    assert space.download_need(600, 1080) == 10 * 25 * 2**20
    assert space.download_need(600, 900) == 10 * 25 * 2**20                 # rounded up to the next known height
    assert space.download_need(600, 0) == 10 * 110 * 2**20                  # no limit: the largest


def test_a_download_refused_before_it_starts(tmp_path, monkeypatch):
    ended = []
    job = downloads.DownloadJob("i", "https://x.example/v", ["yt-dlp"], tmp_path, "v", ended.append)
    job.need = 50 * GB
    monkeypatch.setattr(space.shutil, "disk_usage", lambda p: Usage(0, 0, 1 * GB))
    monkeypatch.setattr(downloads.source, "download_youtube", lambda *a, **k: pytest.fail("downloaded anyway"))
    job.run()
    assert job.video is None and "place" in job.error.lower() or "space" in job.error.lower()
    assert ended == [job]


def test_security_headers_on_every_answer(monkeypatch):
    from subtitles.web import server
    sent = {}

    class Fake(server.Handler):
        def __init__(self):
            pass

        def send_header(self, k, v):
            sent[k] = v

    Fake()._security_headers()
    csp = sent["Content-Security-Policy"]
    assert sent["X-Frame-Options"] == "DENY" and "frame-ancestors 'none'" in csp
    assert "script-src 'self';" in csp and "default-src 'none'" in csp and "unsafe" not in csp     # no inline code, no eval
    assert sent["X-Content-Type-Options"] == "nosniff" and sent["Referrer-Policy"] == "no-referrer"

def test_pages_have_no_inline_code():
    """The Content Security Policy runs only script files: an inline <script> or an on...= handler would be blocked."""
    import re
    static =Path(__file__).resolve().parent.parent / "subtitles" / "web" / "static"
    for page in static.rglob("*.html"):
        text = page.read_text(encoding="utf-8")
        assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", text), page
        assert not re.search(r"\son[a-z]+\s*=", text), page
        assert "javascript:" not in text, page
