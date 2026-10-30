"""P4-H: the English React storefront is served at / on the Gateway.

- An explicit page allowlist gets the store's entry page: /, /checkout, /orders and
  /orders/<order number or attempt reference>, for GET and HEAD only. Anything else is not a
  page: no catch-all, so an API error, /unavailable or a typo is still a 404 with telemetry.
- Only those page requests (and the /ui/ files) are left out of business telemetry, and only on
  the Gateway: the orders service has real API routes at /orders and /orders/{order_id}.
- The legacy Arabic store is removed; the Arabic operator UI on the controller and its shared
  files stay. The bundle keeps its /ui/next/assets/ URLs; old /ui/next/ page links redirect.

Builds are fakes written to a temporary folder named by UI_BUNDLE_DIR (fixture ``builds``).
"""

from pathlib import Path

import httpx
import pytest

from controllers.fault_injector.app import create_app as controller_app
from services.shared import ui

LEGACY = Path(__file__).resolve().parents[2] / "services" / "ui"
PAGES = (
    "/",
    "/checkout",
    "/orders",
    "/orders/OBS-1042-7F3A",
    "/orders/ATT-5A0E-0001",
    "/checkout?attempt=ATT-5A0E-0001",
    "/?utm=x",
)
NOT_PAGES = (
    "/checkout/",
    "/orders/",
    "/orders/nonsense",
    "/orders/ATT-1",
    "/orders/att-5a0e-0001",
    "/orders/OBS-1042-7F3A/items",
    "/Checkout",
    "/checkout.html",
    "/index.html",
    "/unavailable",
    "/api/unknown",
    "/cart",
    # Control characters: Starlette's URL drops tab, CR and LF, so the check must use the raw path.
    "/checkout%0A",
    "/%0A",
    "/orders/OBS-1-AB%09CD",
    "/orders%09",
)


# The framework's usual trailing-slash redirect to the page (as for every API route): recorded.
SLASHED = {"/checkout/": "/checkout", "/orders/": "/orders"}


def spans(mesh, service):
    """Finished spans, after flushing the batch processor (otherwise this is always empty)."""
    mesh["apps"][service].state.telemetry.traces.force_flush()
    return mesh["exporters"][service].get_finished_spans()


@pytest.fixture
async def controller(tmp_path):
    app = controller_app(
        path=tmp_path / "incidents.jsonl",
        client=httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200))),
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://127.0.0.1:8090") as client:
            yield client


async def test_the_store_entry_page_is_served_at_each_allowlisted_page(mesh, builds):
    for path in PAGES:
        response = await mesh["client"].get(path)
        assert response.status_code == 200, path
        assert "<title>store</title>" in response.text, path
        assert response.headers["content-type"].startswith("text/html"), path
        assert response.headers["cache-control"] == "no-cache", path
        assert response.headers["x-content-type-options"] == "nosniff", path


async def test_head_on_a_page_gets_the_headers_without_a_body(mesh, builds):
    response = await mesh["client"].head("/orders/ATT-5A0E-0001")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"
    assert response.content == b""


async def test_anything_else_is_a_plain_404_not_the_entry_page(mesh, builds):
    for path in NOT_PAGES:
        response = await mesh["client"].get(path)
        if path in SLASHED:
            assert response.status_code == 307 and response.headers["location"].endswith(SLASHED[path]), path
            continue
        assert response.status_code == 404, path
        assert "<title>store</title>" not in response.text, path


async def test_pages_accept_only_get_and_head(mesh, builds):
    for method in ("POST", "PUT", "DELETE"):
        response = await mesh["client"].request(method, "/checkout")
        assert response.status_code == 405, method
        assert "<title>store</title>" not in response.text


async def test_page_requests_are_not_business_telemetry(mesh, builds):
    before = mesh["logs"].getvalue()
    for path in PAGES:
        assert "x-trace-id" not in (await mesh["client"].get(path)).headers, path
    assert "x-trace-id" not in (await mesh["client"].head("/checkout")).headers
    assert mesh["logs"].getvalue() == before
    assert spans(mesh, "gateway") == ()
    # The same check sees a traced request, so it can fail.
    await mesh["client"].get("/unavailable")
    assert [span.name for span in spans(mesh, "gateway")] == ["GET unmatched"]


async def test_api_calls_unknown_routes_and_fault_traffic_stay_instrumented(mesh, builds):
    traced = {}
    for method, path in [("GET", "/api/products"), ("GET", "/unavailable"), ("POST", "/checkout")]:
        traced[path] = await mesh["client"].request(method, path)
    for path in NOT_PAGES:
        traced[path] = await mesh["client"].get(path)
    for path, response in traced.items():
        assert len(response.headers.get("x-trace-id", "")) == 32, path
    names = {span.name for span in spans(mesh, "gateway")}
    assert "GET /api/products" in names
    assert "GET unmatched" in names  # /unavailable, the traffic generator's missing route
    assert "POST /checkout" in names  # a page route answering 405 is still recorded
    assert "request_received" in mesh["logs"].getvalue()


async def test_the_orders_service_routes_named_like_pages_stay_instrumented(mesh, builds):
    """/orders and /orders/{order_id} are real API routes on the orders service."""
    orders = httpx.AsyncClient(transport=mesh["transport"], base_url="http://orders")
    for path in ("/orders/order-missing", "/orders"):
        response = await orders.get(path)
        assert len(response.headers.get("x-trace-id", "")) == 32, path
    names = {span.name for span in spans(mesh, "orders")}
    assert "GET /orders/{order_id}" in names


async def test_without_a_build_pages_are_a_plain_404(mesh, tmp_path, monkeypatch):
    monkeypatch.setenv("UI_BUNDLE_DIR", str(tmp_path / "nothing"))
    for path in ("/", "/checkout"):
        response = await mesh["client"].get(path)
        assert response.status_code == 404, path
        assert "x-trace-id" not in response.headers, path


async def test_the_bundle_keeps_its_asset_urls_and_old_page_links_redirect(mesh, builds):
    client = mesh["client"]
    asset = await client.get("/ui/next/assets/store-Ab12.js")
    assert asset.status_code == 200
    assert asset.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert (await client.get("/ui/next/licenses/inter-OFL.txt")).status_code == 200
    for old, new in [
        ("/ui/next", "/"),
        ("/ui/next/", "/"),
        ("/ui/next/checkout", "/checkout"),
        ("/ui/next/orders/ATT-5A0E-0001", "/orders/ATT-5A0E-0001"),
        ("/ui/next/checkout?attempt=ATT-5A0E-0001", "/checkout?attempt=ATT-5A0E-0001"),
    ]:
        response = await client.get(old)
        assert response.status_code == 308, old
        assert response.headers["location"] == new, old
    for missing in ("/ui/next/cart", "/ui/next/orders/nonsense", "/ui/next/missing.js", "/ui/next/assets/missing.js"):
        assert (await client.get(missing)).status_code == 404, missing
    # A redirected path is still a /ui/ request: no telemetry for it.
    assert spans(mesh, "gateway") == ()


async def test_the_gateway_no_longer_serves_the_legacy_arabic_store(mesh, builds):
    page = await mesh["client"].get("/")
    assert 'dir="rtl"' not in page.text
    for name in ("store.js", "app.js", "common.js", "style.css", "products.svg"):
        assert (await mesh["client"].get("/ui/assets/" + name)).status_code == 404, name
    config = (await mesh["client"].get("/ui/config")).json()
    assert config["mode"] == "store"


def test_only_the_legacy_store_files_are_removed():
    for removed in ("store.html", "static/store.js", "static/products.svg"):
        assert not (LEGACY / removed).exists(), removed
    for kept in ("control.html", "static/app.js", "static/common.js", "static/control.js", "static/style.css", "static/icons.svg", "static/mark.svg"):
        assert (LEGACY / kept).is_file(), kept
    entry = (LEGACY / "static" / "app.js").read_text("utf-8")
    assert "store.js" not in entry


async def test_the_legacy_operator_ui_and_its_shared_files_still_work(controller):
    page = await controller.get("/")
    assert page.status_code == 200
    assert 'lang="ar" dir="rtl"' in page.text and 'data-page="control"' in page.text
    for name in ("app.js", "common.js", "control.js", "style.css", "icons.svg", "mark.svg"):
        assert (await controller.get("/ui/assets/" + name)).status_code == 200, name
    assert (await controller.get("/ui/assets/store.js")).status_code == 404
    assert (await controller.get("/ui/config")).json()["mode"] == "control"


async def test_the_controller_keeps_the_operator_bundle_at_ui_next_and_has_no_store_pages(controller, builds):
    assert "<title>operator</title>" in (await controller.get("/ui/next/")).text
    for path in ("/checkout", "/orders", "/orders/ATT-5A0E-0001"):
        assert (await controller.get(path)).status_code == 404, path


def test_the_page_allowlist_is_one_shared_predicate():
    for path in ("/", "/checkout", "/orders", "/orders/OBS-1042-7F3A", "/orders/ATT-5A0E-0001"):
        assert ui.is_store_page(path), path
    for path in NOT_PAGES:
        assert not ui.is_store_page(path.split("?")[0]), path


async def test_page_routes_are_not_part_of_the_api(mesh):
    paths = (await mesh["client"].get("/openapi.json")).json()["paths"]
    for page in ("/", "/checkout", "/orders", "/orders/{ref}"):
        assert page not in paths
