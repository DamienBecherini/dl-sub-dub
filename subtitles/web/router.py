"""The routing of the service: a route is a function declared with @route("METHOD", "/api/path/{id}"), its body
fields in its docstring; it receives the Request and returns (code, data), data sent as JSON, or as is if it is a Raw
(a file) or a Stream (Server-Sent Events). server.Handler finds the route and turns the errors into JSON answers."""
import json
import re
from http.server import BaseHTTPRequestHandler
from typing import Callable, NamedTuple
from urllib.parse import parse_qs

from .. import i18n


class RequestError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class Raw(NamedTuple):
    """Response that is not JSON: a file of the page or a translation catalog."""
    body: bytes
    content_type: str


class Stream(NamedTuple):
    """Server-Sent Events: each string the generator gives is written as it comes, until the page leaves."""
    events: object


ROUTES: list[tuple[str, re.Pattern, Callable]] = []


def _compile(pattern: str) -> re.Pattern:
    """"/api/tasks/{id}" → {id}: hexadecimal identifier; "{name:regex}": parameter with its own expression."""
    return re.compile(re.sub(r"\{(\w+)(?::([^}]+))?\}", lambda m: f"(?P<{m.group(1)}>{m.group(2) or '[0-9a-f]+'})", pattern))


def route(method: str, pattern: str):
    """Records the decorated function as the answer to `method pattern`; the parameters of the path are passed by name.

    The function receives the Request and returns (code, data): data is sent as JSON, or as is if it is a Raw."""
    regex = _compile(pattern)

    def register(fn):
        ROUTES.append((method, regex, fn))
        return fn
    return register


def resolve(method: str, path: str) -> tuple[Callable, dict] | None:
    for m, regex, fn in ROUTES:
        match = regex.fullmatch(path)
        if m == method and match:
            return fn, match.groupdict()
    return None


class Request:
    MAX_JSON = 5_000_000

    def __init__(self, handler: BaseHTTPRequestHandler, url):
        self.path = url.path
        self.query = parse_qs(url.query)
        self.headers = handler.headers
        self._rfile = handler.rfile
        self.length = max(0, int(handler.headers.get("Content-Length") or 0))
        self.consumed = 0
        self._body = None

    def arg(self, name: str, default=None):
        """First value of a parameter of the query string."""
        return self.query.get(name, [default])[0]

    def read(self, n: int) -> bytes:
        block = self._rfile.read(min(n, self.length - self.consumed))
        self.consumed += len(block)
        return block

    def json(self) -> dict:
        if self._body is None:
            if self.length > self.MAX_JSON:
                raise RequestError(413, i18n.t("err.body_too_large"))
            body = json.loads(self.read(self.length) or b"{}")
            if not isinstance(body, dict):              # the routes read fields: a list or a number is refused (400)
                raise ValueError(i18n.t("err.body_not_object"))
            self._body = body
        return self._body

    def drain(self):
        """Reads what the route has left of the body: a connection closed on unread data is reset (Windows) and the
        client loses the answer."""
        left = self.length - self.consumed
        if 0 < left <= self.MAX_JSON:
            self.read(left)


def not_found():
    return RequestError(404, i18n.t("err.not_found"))
