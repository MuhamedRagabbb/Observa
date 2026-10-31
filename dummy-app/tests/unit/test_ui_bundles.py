"""The React bundles' files are served under /ui/next/: the store's by the Gateway, the
operator's by the controller. Both bundles' pages are at /: the store's since P4-H
(test_store_cutover.py), the operator's since P5-G1 (test_operator_cutover.py); old /ui/next/
page links redirect there.

Builds are fakes written to a temporary folder named by UI_BUNDLE_DIR.
"""

import re
from pathlib import Path

import httpx
import pytest

from controllers.fault_injector.app import create_app as controller_app
from services.shared import ui

IMMUTABLE = "public, max-age=31536000, immutable"


@pytest.fixture
async def controller(tmp_path):
    app = controller_app(
        path=tmp_path / "incidents.jsonl",
        client=httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200))),
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://127.0.0.1:8090") as client:
            yield client


async def test_the_operator_entry_is_served_without_long_term_caching(controller, builds):
    response = await controller.get("/")
    assert response.status_code == 200
    assert "<title>operator</title>" in response.text
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "no-cache"


async def test_the_operator_has_one_page_and_no_catch_all(controller, builds):
    # The operator's router has the single route /; unknown paths are not client-side routes.
    for path in ("/ui/next/history", "/ui/next/runs/run-1", "/history"):
        response = await controller.get(path)
        assert response.status_code == 404, path
        assert "<title>operator</title>" not in response.text, path
        # Bundle 404s use _not_found(); /history is an unmatched framework route.
        if path.startswith("/ui/next/"):
            assert response.headers["cache-control"] == "no-cache", path


async def test_the_operator_bundle_root_redirects_to_the_page(controller, builds):
    for path in ("/ui/next", "/ui/next/"):
        response = await controller.get(path, follow_redirects=False)
        assert response.status_code == 308, path
        assert response.headers["location"] == "/", path


async def test_hashed_assets_are_cached_for_a_year_with_their_media_type(mesh, builds):
    expected = {
        "/ui/next/assets/store-Ab12.js": "text/javascript",
        "/ui/next/assets/store-Cd34.css": "text/css",
        "/ui/next/assets/inter-latin-Ef56.woff2": "font/woff2",
    }
    for path, media_type in expected.items():
        response = await mesh["client"].get(path)
        assert response.status_code == 200, path
        assert response.headers["content-type"].split(";")[0] == media_type
        assert response.headers["cache-control"] == IMMUTABLE
        assert response.headers["x-content-type-options"] == "nosniff"
    licence = await mesh["client"].get("/ui/next/licenses/inter-OFL.txt")
    assert licence.status_code == 200 and "Open Font License" in licence.text
    assert licence.headers["cache-control"] == "no-cache"


async def test_missing_files_are_404_never_the_entry_page(mesh, builds):
    for path in (
        "/ui/next/assets/store-0000.js",
        "/ui/next/licenses/missing.txt",
        "/ui/next/favicon.ico",
        "/ui/next/orders/report.pdf",
        "/ui/next/assets/",
    ):
        response = await mesh["client"].get(path)
        assert response.status_code == 404, path
        assert "<title>" not in response.text


async def raw_get(app, path):
    """GET ``path`` exactly as a server passes it on. httpx would remove ``..`` before sending,
    so the request goes straight to the ASGI app; ``path`` is already percent-decoded, as in ASGI.
    """
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"gateway")],
        "client": ("127.0.0.1", 50000),
        "server": ("gateway", 80),
    }
    messages = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    await app(scope, receive, send)
    status = next(m["status"] for m in messages if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    return status, body.decode("utf-8", "replace")


async def test_store_bundle_newline_redirect_is_a_documented_pre_existing_p6_quirk(mesh, builds):
    # pre-existing, recorded for P6; do not 'fix' in G1
    # /ui/next%0A arrives percent-decoded in the ASGI scope.
    assert await raw_get(mesh["apps"]["gateway"], "/ui/next\n") == (308, "")


async def test_paths_cannot_escape_the_build(mesh, builds, tmp_path):
    (tmp_path / "secret.txt").write_text("not public", "utf-8")
    assert await raw_get(mesh["apps"]["gateway"], "/ui/next/assets/store-Ab12.js") == (
        200,
        "console.log('store')",
    )
    for path in (
        "/ui/next/assets/../../../secret.txt",
        "/ui/next/assets/..\\..\\..\\secret.txt",
        "/ui/next/licenses/../../../secret.txt",
        "/ui/next/assets/../index.html",
        "/ui/next/assets/../../operator/assets/operator-Ab12.js",
        "/ui/next/../operator/index.html",
    ):
        status, body = await raw_get(mesh["apps"]["gateway"], path)
        assert status == 404, path
        assert "not public" not in body and "<title>" not in body and "operator" not in body, path


async def test_each_asset_has_exactly_one_url(mesh, builds):
    # Case-insensitive file systems (Windows) would otherwise serve these as the same file.
    for path in (
        "/ui/next/assets/STORE-AB12.JS",
        "/ui/next/assets/store-Ab12.js.",
        "/ui/next/assets/store-Ab12.js ",
    ):
        assert (await mesh["client"].get(path)).status_code == 404, path


async def test_the_bundle_root_redirect_keeps_the_query(mesh, builds, controller):
    # Both bundles' pages are at /: the store's since P4-H, the operator's since P5-G1.
    response = await controller.get("/ui/next?ref=mail", follow_redirects=False)
    assert response.status_code == 308
    assert response.headers["location"] == "/?ref=mail"
    response = await mesh["client"].get("/ui/next?ref=mail", follow_redirects=False)
    assert response.headers["location"] == "/?ref=mail"


async def test_source_maps_and_icons_get_their_media_types(mesh, builds):
    assets = builds / "store" / "assets"
    (assets / "store-Ab12.js.map").write_text("{}", "utf-8")
    (assets / "icon-Gh78.ico").write_bytes(b"\x00\x00\x01\x00")
    (assets / "manifest-Ij90.json").write_text("{}", "utf-8")
    expected = {"store-Ab12.js.map": "application/json", "icon-Gh78.ico": "image/x-icon"}
    expected["manifest-Ij90.json"] = "application/json"
    for name, media_type in expected.items():
        response = await mesh["client"].get(f"/ui/next/assets/{name}")
        assert response.headers["content-type"].split(";")[0] == media_type, name


async def test_each_origin_serves_only_its_own_bundle(mesh, builds, controller):
    gateway = mesh["client"]
    assert (await gateway.get("/ui/next/assets/operator-Ab12.js")).status_code == 404
    assert (await controller.get("/ui/next/assets/store-Ab12.js")).status_code == 404
    page = await controller.get("/")
    assert page.status_code == 200 and "<title>operator</title>" in page.text
    assert page.headers["cache-control"] == "no-cache"
    asset = await controller.get("/ui/next/assets/operator-Ab12.js")
    assert asset.status_code == 200 and asset.headers["cache-control"] == IMMUTABLE


async def test_a_missing_build_is_a_404_and_no_legacy_page_stands_in(
    mesh, controller, tmp_path, monkeypatch
):
    monkeypatch.setenv("UI_BUNDLE_DIR", str(tmp_path / "never-built"))
    for client in (mesh["client"], controller):
        for path in (
            "/ui/next/", "/ui/next/orders", "/ui/next/assets/store-Ab12.js", "/ui/next/assets/operator-Ab12.js"
        ):
            response = await client.get(path)
            assert response.status_code == 404, path
    # Each origin's / is its React bundle only: the legacy Arabic store was removed in P4-H and
    # the legacy Arabic operator is no longer served since P5-G1.
    for client in (mesh["client"], controller):
        response = await client.get("/")
        assert response.status_code == 404
        assert 'dir="rtl"' not in response.text


async def test_bundle_requests_are_not_business_telemetry(mesh, builds):
    before = mesh["logs"].getvalue()
    for path in ("/ui/next/", "/ui/next/orders", "/ui/next/cart", "/ui/next/assets/store-Ab12.js", "/ui/next/missing.js"):
        response = await mesh["client"].get(path)
        assert "x-trace-id" not in response.headers, path
    assert mesh["logs"].getvalue() == before
    mesh["apps"]["gateway"].state.telemetry.traces.force_flush()  # spans are batched
    assert mesh["exporters"]["gateway"].get_finished_spans() == ()
    # A real API call is still traced, so the check above can fail.
    assert len((await mesh["client"].get("/api/products")).headers["x-trace-id"]) == 32


async def test_bundle_routes_are_not_part_of_either_api(mesh, controller):
    for schema in (
        (await mesh["client"].get("/openapi.json")).json(),
        (await controller.get("/openapi.json")).json(),
    ):
        assert not [path for path in schema["paths"] if path.startswith("/ui")]


def test_the_web_build_and_the_server_agree_on_the_bundle_path():
    source = (Path(__file__).resolve().parents[2] / "web" / "src" / "shared" / "bundle-path.ts").read_text(
        "utf-8"
    )
    declared = re.search(r'BUNDLE_PATH = "([^"]+)"', source)
    assert declared and declared.group(1) == ui.BUNDLE_PATH + "/"
    # The cross-app link targets: the store at / since P4-H, the operator at / since P5-G1.
    links = dict(re.findall(r'^\s+(store|control): "([^"]+)"', source, flags=re.M))
    assert links == {"store": "/", "control": "/"}


def test_the_browser_test_harness_serves_the_same_store_pages_as_the_server():
    """The mocked Playwright harness mirrors the server's page allowlist (services/shared/ui.py)."""
    harness = (Path(__file__).resolve().parents[2] / "web" / "e2e" / "support" / "harness.ts").read_text("utf-8")
    declared = re.search(r'STORE_PAGE_PATTERN = "([^"]+)";', harness)
    assert declared, "harness.ts must declare STORE_PAGE_PATTERN"
    assert declared.group(1) == ui.STORE_PAGE.pattern


async def test_head_requests_get_the_same_headers_without_a_body(mesh, builds, controller):
    for client, path, cache in (
        (controller, "/", "no-cache"),
        (mesh["client"], "/ui/next/assets/store-Ab12.js", IMMUTABLE),
    ):
        response = await client.head(path)
        assert response.status_code == 200, path
        assert response.headers["cache-control"] == cache
        assert response.content == b""
    assert (await mesh["client"].head("/ui/next/assets/missing.js")).status_code == 404
