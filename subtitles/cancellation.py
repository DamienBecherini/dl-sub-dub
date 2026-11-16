"""A cancellation asked once, heard at once.

The work of a video takes a `cancelled` callable and asks it between two pieces of work (two chunks of a translation,
two videos of a list). But most of the time it is waiting for something else: yt-dlp downloading, the server
transcribing, the dubbing program generating a voice. Looking at a flag several times a second while waiting is
slow to react and easy to get wrong (a wait that blocks on something else never looks). So the thing that waits
*listens* instead: it gives `listen` what stops what it is waiting for (kill the program, close the connection), and
is told the moment Stop is asked.

`Cancellation` is both: called, it answers whether the cancellation was asked (`cancelled()`, as everywhere in the
chain); `cancel()` asks it and tells the listeners, from the thread that asks. A task and a download own one.
`listen(cancelled, tell)` also takes a plain callable (a test, the command line): nothing can tell when such a
callable turns true, so it is asked every half second by a thread, the only way with it.
"""
import threading


class Cancellation:
    def __init__(self):
        self._lock = threading.Lock()
        self._asked = False
        self._listeners: list = []

    def __call__(self) -> bool:
        return self._asked

    def cancel(self):
        """Asks the cancellation (once) and tells every listener, in the order they came."""
        with self._lock:
            if self._asked:
                return
            self._asked = True
            listeners, self._listeners = self._listeners, []
        for tell in listeners:
            _told(tell)

    def listen(self, tell):
        """`tell()` is called when the cancellation is asked: at once if it already was. Returns the function that
        stops listening (the work it was waiting for is over)."""
        with self._lock:
            if not self._asked:
                self._listeners.append(tell)
                return lambda: self._forget(tell)
        _told(tell)
        return lambda: None

    def _forget(self, tell):
        with self._lock:
            if tell in self._listeners:
                self._listeners.remove(tell)


def _told(tell):
    try:
        tell()
    except Exception:                          # noqa: BLE001 (a listener that fails must not keep the others from hearing)
        pass


def listen(cancelled, tell, every: float = 0.5):
    """Tells `tell()` when `cancelled` turns true; returns the function that stops listening. A Cancellation tells at
    once; any other callable is asked every `every` seconds."""
    if hasattr(cancelled, "listen"):
        return cancelled.listen(tell)
    over = threading.Event()

    def ask():
        while not over.wait(every):
            if cancelled():
                _told(tell)
                return

    threading.Thread(target=ask, daemon=True, name="cancellation").start()
    return over.set
