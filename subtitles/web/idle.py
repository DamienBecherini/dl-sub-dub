"""Automatic shutdown of the interface after a period without activity (`[web] idle_min`, 0 = never).

Counts as activity: an action of the user (POST or PUT request, loading of the page or of data asked for by hand)
and all the time a task or a project is working. Does not count: the regular polling by a tab left open (the projects
and their events included), nor the health check of the New Tab page, otherwise the service would never stop.
"""
import re
import time

_POLLING = re.compile(r"^/api/(health|status|tasks|projects|projects|dubbing/gpu-check)(/|$)")


def is_activity(method: str, path: str) -> bool:
    return method != "GET" or _POLLING.match(path.split("?", 1)[0]) is None


class IdleWatch:
    def __init__(self, timeout_s, busy, clock=time.monotonic):
        self.timeout_s = timeout_s             # function: read again at each check (a change in Settings applies at once)
        self.busy = busy                       # function: True as long as a task or a project is working
        self.clock = clock
        self.last = clock()
        self.leaving = False                   # the window of the application closed: stop once the work is done

    def touch(self):
        self.last = self.clock()

    def leave(self, on: bool = True):
        self.leaving = on

    def expired(self) -> bool:
        if self.busy():
            self.touch()                       # the countdown starts from the end of the work
            return False
        if self.leaving:
            return True
        timeout = self.timeout_s()
        return timeout > 0 and self.clock() - self.last >= timeout

    def remaining_s(self) -> float | None:
        """Seconds before the shutdown; None if no countdown is running (work in progress, or shutdown disabled)."""
        timeout = self.timeout_s()
        if self.busy():
            return None
        if self.leaving:
            return 0.0
        if timeout <= 0:
            return None
        return max(0.0, timeout - (self.clock() - self.last))
