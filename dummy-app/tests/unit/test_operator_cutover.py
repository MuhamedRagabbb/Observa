"""P5-G1: the React operator is served at / on the controller.

- The operator has exactly one page, /, for GET and HEAD. Anything else is not a page: no
  catch-all, so a typo or a store path is a plain 404.
- Old /ui/next/ page links redirect to / (308, query kept); the bundle keeps its
  /ui/next/assets/ and /ui/next/licenses/ URLs. Other /ui/next/ paths are a plain 404.
- The legacy Arabic operator is no longer served (no control.html at /, no /ui/assets/), but
  its source files stay in the repository so the change can be reverted with one commit.
- /ui/config, the fault API and the Storefront are unchanged.

Builds are fakes written to a temporary folder named by UI_BUNDLE_DIR (fixture ``builds``).
"""

from pathlib import Path
from urllib.parse import quote

import httpx
import pytest

from controllers.fault_injector.app import create_app as controller_app

LEGACY = Path(__file__).resolve().parents[2] / "services" / "ui"
LEGACY_FILES = ("app.js", "common.js", "control.js", "style.css", "icons.svg", "mark.svg")
NOT_PAGES = (
    "/index.html",
    "/ui",
    "/ui/",
    "/history",
    "/checkout",
    "/orders",
    "/orders/ATT-5A0E-0001",
    "/unavailable",
    "/control.html",
    "/%0A",
)


@pytest.fixture
async def controller_asgi(tmp_path):
    app = controller_app(
        path=tmp_path / "incidents.jsonl",
        client=httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200))),
    )
    async with app.router.lifespan_context(app):
        yield app


@pytest.fixture
async def controller(controller_asgi):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(controller_asgi), base_url="http://127.0.0.1:8090"
    ) as client:
        yield client


async def raw_request(app, method, path):
    """Pass the already-decoded path directly to ASGI, as raw_get in test_ui_bundles does."""
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": quote(path, safe="/").encode("ascii"),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"127.0.0.1:8090")],
        "client": ("127.0.0.1", 50000),
        "server": ("127.0.0.1", 8090),
    }
    messages = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    await app(scope, receive, send)
    start = next(m for m in messages if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    return httpx.Response(start["status"], headers=start["headers"], content=body)


async def test_the_operator_entry_page_is_served_at_the_root(controller, builds):
    for path in ("/", "/?utm=x"):
        response = await controller.get(path)
        assert response.status_code == 200, path
        assert "<title>operator</title>" in response.text, path
        assert response.headers["content-type"].startswith("text/html"), path
        assert response.headers["cache-control"] == "no-cache", path
        assert response.headers["x-content-type-options"] == "nosniff", path


async def test_head_on_the_root_gets_the_headers_without_a_body(controller, builds):
    response = await controller.head("/")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"
    assert response.content == b""


async def test_the_root_is_not_the_legacy_arabic_operator(controller, builds):
    page = await controller.get("/")
    assert 'dir="rtl"' not in page.text
    assert 'data-page="control"' not in page.text
    assert 'id="auto-form"' not in page.text


async def test_anything_else_is_a_plain_404_not_the_entry_page(controller, builds):
    for path in NOT_PAGES:
        response = await controller.get(path)
        assert response.status_code == 404, path
        assert "<title>operator</title>" not in response.text, path


async def test_the_root_page_accepts_only_get_and_head(controller, builds):
    for method in ("PUT", "DELETE", "PATCH"):
        response = await controller.request(method, "/")
        assert response.status_code in (403, 405), method
        assert "<title>operator</title>" not in response.text, method


async def test_old_ui_next_page_links_redirect_to_the_root(controller, builds):
    for old, new in [
        ("/ui/next", "/"),
        ("/ui/next/", "/"),
        ("/ui/next/?utm=x", "/?utm=x"),
    ]:
        response = await controller.get(old)
        assert response.status_code == 308, old
        assert response.headers["location"] == new, old
    head = await controller.head("/ui/next/")
    assert head.status_code == 308 and head.headers["location"] == "/"


async def test_other_ui_next_paths_are_a_plain_404(controller, builds):
    for path in ("/ui/next/history", "/ui/next/runs/run-1", "/ui/next/checkout", "/ui/next/missing.js", "/ui/next/assets/missing.js"):
        response = await controller.get(path)
        assert response.status_code == 404, path
        assert "<title>operator</title>" not in response.text, path


@pytest.mark.parametrize("method", ("GET", "HEAD"))
@pytest.mark.parametrize(
    "path, status",
    (
        ("/ui/next", 308),
        ("/ui/next/", 308),
        ("/ui/next/assets/operator-Ab12.js", 200),
        ("/ui/next/licenses/inter-OFL.txt", 200),
    ),
)
async def test_ui_next_paths_with_a_final_newline_are_plain_404s(controller_asgi, builds, method, path, status):
    # First prove the exact form works through the same raw ASGI request, with a build present.
    valid = await raw_request(controller_asgi, method, path)
    assert valid.status_code == status, (method, path)
    if status == 308:
        assert valid.headers["location"] == "/"
    # The malformed URL is path + %0A; ASGI receives the decoded newline.
    malformed = path + "\n"
    response = await raw_request(controller_asgi, method, malformed)
    assert response.status_code == 404, (method, repr(malformed))
    assert "location" not in response.headers
    assert response.headers["content-type"].startswith("text/plain")
    assert "<title>" not in response.text and "<html" not in response.text
    if method == "GET":
        assert response.text == "Not found"


@pytest.mark.parametrize("method", ("POST", "PUT", "DELETE"))
@pytest.mark.parametrize("path", ("/ui/next/", "/ui/next"))
async def test_non_get_requests_to_old_page_links_are_refused(controller, builds, method, path):
    response = await controller.request(method, path, follow_redirects=False)
    assert response.status_code in (403, 405), (method, path, response.status_code)
    assert "location" not in response.headers
    assert "<title>operator</title>" not in response.text


async def test_the_bundle_keeps_its_asset_and_licence_urls(controller, builds):
    asset = await controller.get("/ui/next/assets/operator-Ab12.js")
    assert asset.status_code == 200
    assert asset.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert asset.headers["x-content-type-options"] == "nosniff"
    licence = await controller.get("/ui/next/licenses/inter-OFL.txt")
    assert licence.status_code == 200 and "Open Font License" in licence.text
    # The controller serves only the operator bundle, never the store's files.
    assert (await controller.get("/ui/next/assets/store-Ab12.js")).status_code == 404


async def test_the_legacy_operator_assets_are_no_longer_served(controller, builds):
    for name in LEGACY_FILES + ("control.html", "store.js"):
        assert (await controller.get("/ui/assets/" + name)).status_code == 404, name


def test_the_legacy_operator_source_files_are_kept_for_rollback():
    assert (LEGACY / "control.html").is_file()
    for name in LEGACY_FILES:
        assert (LEGACY / "static" / name).is_file(), name


async def test_ui_config_is_unchanged(controller, builds):
    config = (await controller.get("/ui/config")).json()
    assert config["mode"] == "control"
    assert config["ports"] == {"store": 8000, "control": 8090, "grafana": 3000, "jaeger": 16686, "prometheus": 9090}


async def test_without_a_build_the_root_is_a_plain_404_not_the_legacy_page(controller, tmp_path, monkeypatch):
    monkeypatch.setenv("UI_BUNDLE_DIR", str(tmp_path / "nothing"))
    for path in ("/", "/ui/next/"):
        response = await controller.get(path)
        assert response.status_code == 404, path
        assert 'data-page="control"' not in response.text, path
        assert "<title>operator</title>" not in response.text, path
    # Current unbuilt behaviour: the exact slashless old link still redirects to /.
    response = await controller.get("/ui/next", follow_redirects=False)
    assert response.status_code == 308
    assert response.headers["location"] == "/"


async def test_the_fault_api_is_unchanged(controller, builds):
    status = await controller.get("/faults/status")
    assert status.status_code == 200
    assert status.json()["state"] == "idle"
    paths = (await controller.get("/openapi.json")).json()["paths"]
    assert "/faults/status" in paths and "/faults/activate" in paths
    for page in ("/", "/ui/config"):
        assert page not in paths, page
