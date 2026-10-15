"""The web kit: routing, JSON handling, static files and the path-escape guard."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from shared.theme import THEME_CSS, attach_theme
from shared.webkit import App, BackgroundServer, BadRequest, Request, Response


def build_app(tmp_path: Path) -> App:
    app = App("test", static_dir=tmp_path)
    attach_theme(app)

    @app.route("GET", "/api/ping")
    def ping(_request: Request) -> Response:
        return Response.json({"pong": True})

    @app.route("POST", "/api/echo")
    def echo(request: Request) -> Response:
        return Response.json({"got": request.json()})

    @app.route("POST", "/api/refuse")
    def refuse(_request: Request) -> Response:
        raise BadRequest("not like that")

    @app.route("GET", "/api/explode")
    def explode(_request: Request) -> Response:
        raise RuntimeError("deliberate")

    return app


def test_routes_are_registered_once(tmp_path: Path) -> None:
    app = build_app(tmp_path)
    with pytest.raises(ValueError, match="already registered"):

        @app.route("GET", "/api/ping")
        def duplicate(_request: Request) -> Response:  # pragma: no cover
            return Response.json({})


def test_get_and_post(tmp_path: Path) -> None:
    app = build_app(tmp_path)
    assert json.loads(app.handle("GET", "/api/ping").body) == {"pong": True}
    response = app.handle("POST", "/api/echo", b'{"a": 1}')
    assert json.loads(response.body) == {"got": {"a": 1}}


def test_query_string_is_parsed_and_path_matched_without_it(tmp_path: Path) -> None:
    app = App("q", static_dir=tmp_path)

    @app.route("GET", "/api/limit")
    def limit(request: Request) -> Response:
        return Response.json({"limit": request.query.get("limit")})

    assert json.loads(app.handle("GET", "/api/limit?limit=7").body) == {"limit": "7"}


def test_bad_request_becomes_400_with_a_message(tmp_path: Path) -> None:
    response = build_app(tmp_path).handle("POST", "/api/refuse", b"{}")
    assert response.status == 400
    assert json.loads(response.body)["error"] == "not like that"


def test_unexpected_error_becomes_500_that_says_what_happened(tmp_path: Path) -> None:
    response = build_app(tmp_path).handle("GET", "/api/explode")
    assert response.status == 500
    body = json.loads(response.body)
    assert "RuntimeError: deliberate" in body["error"]
    assert "traceback" in body


@pytest.mark.parametrize("body", [b"", b"{oops", b"\xff\xfe"])
def test_malformed_bodies_are_rejected_not_crashed(tmp_path: Path, body: bytes) -> None:
    response = build_app(tmp_path).handle("POST", "/api/echo", body)
    assert response.status == 400
    assert json.loads(response.body)["error"]


def test_unknown_route_is_404(tmp_path: Path) -> None:
    assert build_app(tmp_path).handle("GET", "/api/nothing").status == 404


def test_static_index_is_served_at_root(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<p>hi</p>", encoding="utf-8")
    response = build_app(tmp_path).handle("GET", "/")
    assert response.status == 200
    assert b"hi" in response.body
    assert response.content_type.startswith("text/html")


def test_a_path_that_climbs_out_of_the_static_root_is_refused(tmp_path: Path) -> None:
    secret = tmp_path.parent / "secret.txt"
    secret.write_text("private", encoding="utf-8")
    app = build_app(tmp_path)
    for attempt in ("/../secret.txt", "/..%2fsecret.txt", "/sub/../../secret.txt"):
        response = app.handle("GET", attempt)
        assert response.status in {403, 404}, attempt
        assert b"private" not in response.body


def test_theme_declares_dark_under_both_scopes() -> None:
    # A dark-only block outside its media query fires on every default page, so the
    # media query and the explicit toggle must each carry their own declarations.
    assert "@media (prefers-color-scheme: dark)" in THEME_CSS
    assert ':root:not([data-theme="light"])' in THEME_CSS
    assert ':root[data-theme="dark"]' in THEME_CSS
    assert THEME_CSS.count("--surface:") >= 3


def test_theme_is_served_as_css(tmp_path: Path) -> None:
    response = build_app(tmp_path).handle("GET", "/theme.css")
    assert response.status == 200
    assert response.content_type.startswith("text/css")


def test_background_server_answers_over_real_http_then_closes(tmp_path: Path) -> None:
    import urllib.error
    import urllib.request

    (tmp_path / "index.html").write_text("<p>served</p>", encoding="utf-8")
    app = build_app(tmp_path)
    with BackgroundServer(app) as server:
        base = server.base_url
        assert b"served" in urllib.request.urlopen(base + "/").read()
        assert json.loads(urllib.request.urlopen(base + "/api/ping").read()) == {"pong": True}
    with pytest.raises((urllib.error.URLError, OSError)):
        urllib.request.urlopen(base + "/api/ping", timeout=2)
