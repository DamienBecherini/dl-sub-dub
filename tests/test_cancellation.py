"""A cancellation is heard the moment it is asked: what waits for a program or a connection listens to it, instead of
looking at a flag several times a second."""
import sys
import threading
import time
from pathlib import Path

import pytest

from subtitles import cancellation, dubbing, source, steps
from subtitles.cancellation import Cancellation
from subtitles.web.tasks import Task


def test_listeners_are_told_once_at_once_and_can_leave():
    c = Cancellation()
    heard = []
    c.listen(lambda: heard.append("a"))
    forget = c.listen(lambda: heard.append("b"))
    c.listen(lambda: 1 / 0)                                 # a listener that fails does not keep the others from hearing
    c.listen(lambda: heard.append("c"))
    forget()
    assert not c() and heard == []
    c.cancel()
    c.cancel()                                              # asked twice: told once
    assert c() and heard == ["a", "c"]
    c.listen(lambda: heard.append("late"))                  # already asked: told at once
    assert heard == ["a", "c", "late"]


def test_a_plain_callable_is_asked_now_and_then():
    flag, heard = [], threading.Event()
    forget = cancellation.listen(lambda: bool(flag), heard.set, every=0.05)
    assert not heard.wait(0.2)
    flag.append(1)
    assert heard.wait(2)
    forget()
    never = threading.Event()
    cancellation.listen(lambda: True, never.set, every=0.05)()      # left before the first look: never told
    assert not never.wait(0.2)


def test_a_task_owns_its_cancellation():
    t = Task({"source": "v.mp4"})
    heard = []
    t.cancellation.listen(lambda: heard.append(1))
    assert not t.cancel_requested
    t.cancel_requested = True
    assert t.cancel_requested and t.cancellation() and heard == [1]


def test_a_program_awaited_is_killed_the_moment_the_cancellation_is_asked():
    c = Cancellation()
    threading.Timer(0.5, c.cancel).start()
    t0 = time.monotonic()
    assert source._run_cancellable([sys.executable, "-c", "import time; time.sleep(60)"], c) is None
    assert time.monotonic() - t0 < 10
    assert source._run_cancellable([sys.executable, "-c", "raise SystemExit(0)"], Cancellation()) == 0


def test_a_silent_dubbing_stops_the_moment_the_cancellation_is_asked(monkeypatch, tmp_path):
    fake = tmp_path / "fake.py"
    fake.write_text("import json, sys, time\njson.loads(sys.stdin.readline())\ntime.sleep(60)\n", encoding="utf-8")
    monkeypatch.setattr(dubbing, "WORKER", fake)
    c = Cancellation()
    threading.Timer(0.5, c.cancel).start()
    t0 = time.monotonic()
    with pytest.raises(dubbing.DubbingError):
        dubbing.dub({"dubbing": {"enabled": True, "command": [sys.executable], "model": "m"}}, Path("v.mp4"), tmp_path, Path("a.srt"),
                    Path("b.srt"), "fr", log=lambda _m: None, cancelled=c)
    assert time.monotonic() - t0 < 10 and not steps.dubbing_file(tmp_path, "fr").exists()
