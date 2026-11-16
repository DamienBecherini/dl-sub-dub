"""Work started in a thread of its own (reading a site, adding tracks, giving the graphics card back): one function,
so that the tests run it by hand or not at all (`monkeypatch.setattr(background, "spawn", …)`)."""
import threading


def spawn(name: str, target, *args):
    threading.Thread(target=target, args=args, daemon=True, name=name).start()
