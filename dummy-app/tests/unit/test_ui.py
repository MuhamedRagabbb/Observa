import httpx

from controllers.fault_injector.app import create_app


async def test_store_ui_config_requests_are_not_application_telemetry(mesh):
    """The Gateway's /ui/config (the React store reads it for cross-app links). The legacy
    Arabic store page and its files were removed in P4-H (see test_store_cutover.py)."""
    before = mesh["logs"].getvalue()
    client = mesh["client"]
    response = await client.get("/ui/config")
    assert "x-trace-id" not in response.headers
    config = response.json()
    assert config["mode"] == "store"
    assert config["ports"]["control"] == 8090
    assert "local-control-only" not in str(config)
    assert (await client.get("/ui/assets/control.html")).status_code == 404
    assert mesh["logs"].getvalue() == before
    # Actual shopping API calls continue to produce correlated telemetry.
    products = await client.get("/api/products")
    assert products.status_code == 200
    assert len(products.headers["x-trace-id"]) == 32


async def test_public_ports_follow_configuration_without_exposing_credentials(mesh, monkeypatch):
    monkeypatch.setenv("PUBLIC_CONTROLLER_PORT", "18090")
    monkeypatch.setenv("PUBLIC_JAEGER_PORT", "26686")
    response = await mesh["client"].get("/ui/config")
    assert response.json()["ports"]["control"] == 18090
    assert response.json()["ports"]["jaeger"] == 26686
    assert "DATABASE_URL" not in response.text
    schema = (await mesh["client"].get("/openapi.json")).json()
    assert "/" not in schema["paths"]
    assert "/ui/config" not in schema["paths"]


async def test_operator_interface_uses_controller_origin(tmp_path):
    app = create_app(
        path=tmp_path / "incidents.jsonl",
        client=httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200))),
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://controller"
        ) as client:
            page = await client.get("/")
            assert page.status_code == 200
            assert 'data-page="control"' in page.text
            assert 'id="auto-form"' in page.text
            assert (await client.get("/ui/config")).json()["mode"] == "control"
            assert (await client.get("/ui/assets/control.js")).status_code == 200
            assert (
                await client.post(
                    "/faults/activate", json={"scenario": "payments_errors", "duration_seconds": 1}
                )
            ).status_code == 200
            assert (await client.post("/faults/auto/stop")).json()["active"] is None
