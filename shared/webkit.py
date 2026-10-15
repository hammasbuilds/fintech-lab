"""A small HTTP kit on top of ``http.server``.

Each product serves one single-page UI and a handful of JSON endpoints. That is all this
needs to do, so it is a router, a JSON helper and a static-file handler and nothing else.

The design point worth stating: ``App.handle`` is a pure function from
``(method, path, body)`` to ``Response``. The socket server is a thin shell around it, so
every endpoint is testable without binding a port, and the test suite never opens one.
"""

from __future__ import annotations

import json
import mimetypes
import threading
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

MAX_BODY_BYTES = 1 << 20  # 1 MiB; every request here is a short form post


@dataclass
class Request:
    method: str
    path: str
    body: bytes = b""
    query: dict[str, str] = field(default_factory=dict)

    def json(self) -> Any:
        """Parse the body as JSON, or raise ``BadRequest`` with a usable message."""
        if not self.body:
            raise BadRequest("request body is empty; expected JSON")
        try:
            return json.loads(self.body.decode("utf-8"))
        except UnicodeDecodeError as exc:
            raise BadRequest(f"body is not valid UTF-8: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise BadRequest(f"body is not valid JSON: {exc.msg} at position {exc.pos}") from exc


@dataclass
class Response:
    status: int = 200
    body: bytes = b""
    content_type: str = "application/json; charset=utf-8"

    @classmethod
    def json(cls, payload: Any, status: int = 200) -> Response:
        data = json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")
        return cls(status=status, body=data)

    @classmethod
    def text(cls, body: str, status: int = 200, content_type: str = "text/plain") -> Response:
        return cls(
            status=status,
            body=body.encode("utf-8"),
            content_type=f"{content_type}; charset=utf-8",
        )


class BadRequest(Exception):
    """A client error. Carries a message meant to be shown in the UI."""


class App:
    """Routes exact paths to handlers. No pattern matching: these UIs do not need it."""

    def __init__(self, name: str, static_dir: Path | None = None) -> None:
        self.name = name
        self.static_dir = static_dir
        self._routes: dict[tuple[str, str], Callable[[Request], Response]] = {}

    def route(self, method: str, path: str) -> Callable[..., Callable[[Request], Response]]:
        def register(fn: Callable[[Request], Response]) -> Callable[[Request], Response]:
            key = (method.upper(), path)
            if key in self._routes:
                raise ValueError(f"route already registered: {method} {path}")
            self._routes[key] = fn
            return fn

        return register

    @property
    def routes(self) -> list[tuple[str, str]]:
        return sorted(self._routes)

    def handle(self, method: str, path: str, body: bytes = b"") -> Response:
        """The whole app as one function. The socket server only calls this."""
        raw_path, _, raw_query = path.partition("?")
        query = _parse_query(raw_query)
        handler = self._routes.get((method.upper(), raw_path))
        if handler is not None:
            request = Request(method=method.upper(), path=raw_path, body=body, query=query)
            try:
                return handler(request)
            except BadRequest as exc:
                return Response.json({"error": str(exc)}, status=400)
            except Exception as exc:  # the UI needs to see the failure, not a blank 500
                return Response.json(
                    {
                        "error": f"{type(exc).__name__}: {exc}",
                        "traceback": traceback.format_exc(limit=6),
                    },
                    status=500,
                )
        static = self._serve_static(raw_path)
        if static is not None:
            return static
        return Response.json({"error": f"no route for {method.upper()} {raw_path}"}, status=404)

    def _serve_static(self, path: str) -> Response | None:
        if self.static_dir is None or not path.startswith("/"):
            return None
        relative = "index.html" if path == "/" else path.lstrip("/")
        root = self.static_dir.resolve()
        try:
            target = (root / relative).resolve()
        except OSError:
            return None
        # Keep the handler inside the static root even if the path climbs out of it.
        if target != root and root not in target.parents:
            return Response.json({"error": "path escapes the static root"}, status=403)
        if not target.is_file():
            return None
        guessed, _ = mimetypes.guess_type(target.name)
        return Response(
            status=200,
            body=target.read_bytes(),
            content_type=f"{guessed or 'application/octet-stream'}; charset=utf-8",
        )


def _parse_query(raw: str) -> dict[str, str]:
    from urllib.parse import parse_qsl

    return dict(parse_qsl(raw, keep_blank_values=True))


def serve(app: App, host: str = "127.0.0.1", port: int = 0) -> ThreadingHTTPServer:
    """Bind ``app`` to a socket and return the server without starting its loop.

    Port 0 asks the OS for a free port, which is what the smoke tests use. The caller
    decides whether to run the loop in the foreground or a thread, so nothing here
    starts listening as a side effect of being imported.
    """

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = f"fintech-lab/{app.name}"

        def _dispatch(self, method: str) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY_BYTES:
                response = Response.json({"error": "request body too large"}, status=413)
            else:
                response = app.handle(method, self.path, self.rfile.read(length) if length else b"")
            self.send_response(response.status)
            self.send_header("Content-Type", response.content_type)
            self.send_header("Content-Length", str(len(response.body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(response.body)

        def do_GET(self) -> None:  # noqa: N802 - name fixed by BaseHTTPRequestHandler
            self._dispatch("GET")

        def do_POST(self) -> None:  # noqa: N802 - name fixed by BaseHTTPRequestHandler
            self._dispatch("POST")

        def log_message(self, fmt: str, *args: Any) -> None:
            return  # the UI is the output; an access log on stdout only hides it

    return ThreadingHTTPServer((host, port), Handler)


def run(app: App, host: str = "127.0.0.1", port: int = 8000) -> None:
    """Run ``app`` in the foreground until interrupted. Only ever called from a CLI."""
    server = serve(app, host, port)
    bound_host, bound_port = server.server_address[:2]
    print(f"{app.name} on http://{bound_host}:{bound_port}  (ctrl-c to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()


class BackgroundServer:
    """A context manager that serves ``app`` on a free port for the length of a block.

    The smoke tests use this to prove the UI and its endpoints answer over real HTTP,
    then close the socket. Nothing is left listening.
    """

    def __init__(self, app: App, host: str = "127.0.0.1") -> None:
        self._server = serve(app, host, 0)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def __enter__(self) -> BackgroundServer:
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._server.shutdown()
        self._thread.join(timeout=5)
        self._server.server_close()
