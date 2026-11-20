"""Local HTTP server of the interface (standard library, like the supervision): the checks made on every request
(Host, Origin, Sec-Fetch-Site), the answer written (JSON, a file, a stream), the stop after an hour without activity.

The routes are in routes/, one file per family of addresses (pages, projects, videos, system); each is a function
declared with @route (router.py). Handler._dispatch finds it, turns the errors into JSON answers and sends the result.
What the routes share (the work of the service, `manager`) is in service.py.
"""
import json
import logging
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .. import cleanup, config, i18n, local_transcription
from . import routes, service  # noqa: F401 (routes: importing it records them)
from .idle import is_activity
from .router import Raw, Request, RequestError, Stream, not_found, resolve

log = logging.getLogger("subtitles.web")


# Content Security Policy of every answer: only the scripts and style sheets of the service itself run (no inline code,
# nothing from elsewhere, no eval), the page talks only to the service, a picture may come from a video site (the
# thumbnail of an address) or be inline (the icon), no plugin, no <base>, forms only here, never in a frame. A script
# slipped into a title or a subtitle by a video site could not run, nor send anything elsewhere.
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data: https:; connect-src 'self'; "
       "font-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    server_version = "subtitles"

    def parse_request(self):
        ok = super().parse_request()
        if ok:
            page_language = self.headers.get("X-Language")
            i18n.for_request(page_language)
            i18n.note_page_language(page_language)
        if ok and is_activity(self.command, self.path):
            service.idle.touch()
        return ok

    def log_message(self, fmt, *args):
        if self.path.startswith("/api/tasks/") and self.command == "GET":
            return
        log.info("%s %s", self.command, self.path)

    def do_GET(self):
        self._dispatch()

    do_POST = do_PUT = do_GET

    def _security_headers(self):
        """On every answer: the page cannot be shown in a frame of another site (a click there would act here), the
        browser never guesses another type than the one sent, and the address of the page is not sent elsewhere."""
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")

    def _foreign(self) -> bool:
        """Request that does not come from the page of the interface: refused before any route.

        Host other than 127.0.0.1 or localhost on our port: DNS rebinding (a site whose name now points to this PC).
        POST or PUT with the Origin of another site: a page open in the browser that writes to the API (CSRF); a
        browser always sends Origin with these methods, other clients (curl, command line) do not send it."""
        port = self.server.server_address[1]
        ours = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if (self.headers.get("Host") or "").lower() not in ours:
            return True
        # Sec-Fetch-Site, set by the browser itself: a request made by another site (or another port of this PC, "same-
        # site") is refused, except opening the page from a link (a navigation). A second lock after Origin.
        site = (self.headers.get("Sec-Fetch-Site") or "").lower()
        navigation = self.command == "GET" and (self.headers.get("Sec-Fetch-Mode") or "").lower() == "navigate"
        if site in ("cross-site", "same-site") and not navigation:
            return True
        origin = (self.headers.get("Origin") or "").lower()
        return self.command != "GET" and bool(origin) and origin.removeprefix("http://") not in ours

    def _dispatch(self):
        url = urlparse(self.path)
        req = None
        try:
            req = Request(self, url)               # first: its body is read even when refused (an unread body resets the connection)
            if self._foreign():
                raise RequestError(403, i18n.t("err.foreign_request"))
            found = resolve(self.command, url.path)
            if found is None:
                raise not_found()
            fn, params = found
            code, data = fn(req, **params)
        except RequestError as e:
            code, data = e.code, {"error": str(e)}
        except config.ConfigError as e:
            code, data = 400, {"error": str(e)}
        except (ValueError, KeyError) as e:
            code, data = 400, {"error": i18n.t("err.invalid_request", error=e)}
        except Exception as e:                 # noqa: BLE001 (an answer all the same, rather than a connection cut)
            log.exception("%s %s", self.command, url.path)
            code, data = 500, {"error": i18n.t("err.internal", error=type(e).__name__)}
        if req:
            req.drain()
        self._send(code, data)

    def _send(self, code: int, data):
        if isinstance(data, Stream):
            self._stream(data.events)
            return
        if isinstance(data, Raw):
            body, content_type = data
        else:
            body, content_type = json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8"
        self.send_response(code)
        self._security_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if isinstance(data, Raw):
            self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _stream(self, events):
        """Server-Sent Events: written as they come; HTTP/1.0, so the end of the stream closes the connection."""
        self.send_response(200)
        self._security_headers()
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        try:
            for chunk in events:
                self.wfile.write(chunk.encode("utf-8"))
                self.wfile.flush()
        except OSError:                        # BrokenPipe, ConnectionReset…: the page left
            pass


def _watch_idle(server):
    while True:
        time.sleep(1)
        if service.idle.expired():
            log.info(i18n.t("srv.window_closed") if service.idle.leaving else i18n.t("srv.idle_stop", min=f"{service.idle.timeout_s() / 60:.0f}"))
            server.shutdown()
            return


class Server(ThreadingHTTPServer):
    """One thread a request, none of which holds the stop of the service. The page asks for its files and its data
    all at once, each on a connection of its own (HTTP/1.0): the queue of the connections not accepted yet is long
    enough for them. With the default of 5, a busy machine refused those that did not fit, and the page missed a
    script or an answer (seen by the tests of the page: ERR_CONNECTION_REFUSED on its scripts)."""
    daemon_threads = True
    request_queue_size = 128


def serve(port: int, idle_min: float | None = None):
    if idle_min is not None:
        service.imposed_timeout = idle_min * 60
    service.idle.touch()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", stream=sys.stdout)
    server = Server(("127.0.0.1", port), Handler)
    try:
        cfg = config.load()
        i18n.set_language(cfg.get("ui_language"))
        count, size = cleanup.purge_logs(Path(cfg["output_dir"]) / ".work", float(cleanup.options(cfg)["logs_age_h"]))
        if count:
            log.info(i18n.t("srv.logs_purged", n=count, mb=f"{size / 1e6:.1f}"))
        gone = cleanup.limit_work(Path(cfg["output_dir"]) / ".work", cleanup.options(cfg))
        if gone["files"]:
            log.info(i18n.t("srv.work_purged", n=gone["files"], mb=f"{gone['bytes'] / 1e6:.1f}"))
    except (config.ConfigError, OSError, ValueError, KeyError) as e:
        log.warning(i18n.t("srv.purge_failed", error=e))
    log.info(i18n.t("srv.listening", url=f"http://127.0.0.1:{port}/"))
    threading.Thread(target=_watch_idle, args=(server,), daemon=True, name="idle").start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        service.manager.close()
        for m in config.load().get("transcriptions", []):
            if m.get("command"):
                local_transcription.LocalServer.for_engine(m).stop()
