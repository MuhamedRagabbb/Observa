"""The new React bundles are served under /ui/next/: the store by the Gateway, the operator by
the controller. The legacy Arabic UI keeps / until P4 (store) and P5 (operator) switch it.

Builds are fakes written to a temporary folder named by UI_BUNDLE_DIR.
"""

import re
from pathlib import Path

import httpx
import pytest

from controllers.fault_injector.app import create_app as controller_app
from services.shared import ui

IMMUTABLE = "public, max-age=31536000, immutable"


def write_build(root, name):
    build = root / name
    (build / "assets").mkdir(parents=True)
    (build / "licenses").mkdir()
    (build / "index.html").write_text(f'<!doctype html><html lang="en"><title>{name}</title>', "utf-8")
    (build / "assets" / f"{name}-Ab12.js").write_text(f"console.log('{name}')", "utf-8")
    (build / "assets" / f"{name}-Cd34.css").write_text("body{margin:0}", "utf-8")
    (build / "assets" / "inter-latin-Ef56.woff2").write_bytes(b"wOF2font")
    (build / "licenses" / "inter-OFL.txt").write_text("SIL Open Font License, Version 1.1", "utf-8")
    return build


@pytest.fixture
def builds(tmp_path, monkeypatch):
    root = tmp_path / "dist"
    write_build(root, "store")
    write_build(root, "operator")
    monkeypatch.setenv("UI_BUNDLE_DIR", str(root))
    return root


@pytest.fixture
async def controller(tmp_path):
    app = controller_app(
        path=tmp_path / "incidents.jsonl",
        client=httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200))),
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://ctl") as client:
            yield client


async def test_gateway_serves_the_store_entry_without_long_term_caching(mesh, builds):
    response = await mesh["client"].get("/ui/next/")
    assert response.status_code == 200
    assert "<title>store</title>" in response.text
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "no-cache"


async def test_client_side_routes_get_the_entry_page(mesh, builds):
    for path in ("/ui/next/orders", "/ui/next/orders/ATT-5A0E-0001"):
        response = await mesh["client"].get(path)
        assert response.status_code == 200
        assert "<title>store</title>" in response.text
        assert response.headers["cache-control"] == "no-cache"


async def test_the_bundle_root_without_a_slash_redirects(mesh, builds):
    response = await mesh["client"].get("/ui/next", follow_redirects=False)
    assert response.status_code in (307, 308)
    assert response.headers["location"] == "/ui/next/"


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


async def test_the_bundle_root_redirect_keeps_the_query(mesh, builds):
    response = await mesh["client"].get("/ui/next?ref=mail", follow_redirects=False)
    assert response.headers["location"] == "/ui/next/?ref=mail"


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
    page = await controller.get("/ui/next/")
    assert page.status_code == 200 and "<title>operator</title>" in page.text
    assert page.headers["cache-control"] == "no-cache"
    asset = await controller.get("/ui/next/assets/operator-Ab12.js")
    assert asset.status_code == 200 and asset.headers["cache-control"] == IMMUTABLE


async def test_a_missing_build_is_a_404_and_the_legacy_ui_still_works(
    mesh, controller, tmp_path, monkeypatch
):
    monkeypatch.setenv("UI_BUNDLE_DIR", str(tmp_path / "never-built"))
    for client in (mesh["client"], controller):
        for path in ("/ui/next/", "/ui/next/orders", "/ui/next/assets/store-Ab12.js"):
            response = await client.get(path)
            assert response.status_code == 404, path
        legacy = await client.get("/")
        assert legacy.status_code == 200 and 'lang="ar" dir="rtl"' in legacy.text


async def test_bundle_requests_are_not_business_telemetry(mesh, builds):
    before = mesh["logs"].getvalue()
    for path in ("/ui/next/", "/ui/next/orders", "/ui/next/assets/store-Ab12.js", "/ui/next/missing.js"):
        response = await mesh["client"].get(path)
        assert "x-trace-id" not in response.headers, path
    assert mesh["logs"].getvalue() == before
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
    # The cross-app link targets: both apps are served at /ui/next/ until P4 and P5.
    links = dict(re.findall(r'^\s+(store|control): "([^"]+)"', source, flags=re.M))
    assert links == {"store": ui.BUNDLE_PATH + "/", "control": ui.BUNDLE_PATH + "/"}


async def test_head_requests_get_the_same_headers_without_a_body(mesh, builds):
    for path, cache in (("/ui/next/", "no-cache"), ("/ui/next/assets/store-Ab12.js", IMMUTABLE)):
        response = await mesh["client"].head(path)
        assert response.status_code == 200, path
        assert response.headers["cache-control"] == cache
        assert response.content == b""
    assert (await mesh["client"].head("/ui/next/assets/missing.js")).status_code == 404
