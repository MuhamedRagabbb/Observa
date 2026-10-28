"""The web app's TypeScript types are generated from OpenAPI snapshots committed in web/openapi/.

These tests keep those snapshots equal to the application code, keep evaluation ground truth
out of them, and pin the hand-typed /ui/config contract that the web app shares.
"""

import json
from pathlib import Path

import httpx

from controllers.fault_injector.app import GROUND_TRUTH_FIELDS
from controllers.fault_injector.app import create_app as controller_app
from scripts import export_openapi

WEB = Path(__file__).resolve().parents[2] / "web"
CONTROLLER_ONLY = ("/faults", "/services/health", "/activity", "/_control")


def committed(name):
    return json.loads((WEB / "openapi" / f"{name}.json").read_text(encoding="utf-8"))


def property_names(node):
    """Every property name declared anywhere in a schema document."""
    if isinstance(node, dict):
        names = set(node.get("properties", {})) if isinstance(node.get("properties"), dict) else set()
        return names.union(*(property_names(value) for value in node.values()))
    if isinstance(node, list):
        return set().union(*(property_names(value) for value in node))
    return set()


def shape(value):
    if isinstance(value, dict):
        return {key: shape(item) for key, item in value.items()}
    return type(value).__name__


def test_committed_snapshots_match_the_application_code():
    for name, schema in export_openapi.schemas().items():
        text = (WEB / "openapi" / f"{name}.json").read_text(encoding="utf-8")
        assert text == export_openapi.render(schema), (
            f"web/openapi/{name}.json is stale: run `python -m scripts.export_openapi`, then `npm run gen:api`"
        )


def test_snapshots_cover_each_origin_and_nothing_else():
    gateway, controller = committed("gateway"), committed("controller")
    assert {"/api/products", "/api/cart/quote", "/api/checkout", "/api/orders"} <= set(gateway["paths"])
    assert not [path for path in gateway["paths"] if path.startswith(CONTROLLER_ONLY)]
    assert {"/faults/status", "/faults/activate", "/services/health", "/activity"} <= set(controller["paths"])
    assert not [path for path in controller["paths"] if path.startswith("/api/")]
    for schema in (gateway, controller):
        assert "/" not in schema["paths"] and "/ui/config" not in schema["paths"]


def test_controller_snapshot_carries_no_ground_truth():
    assert GROUND_TRUTH_FIELDS == {"root_cause", "root_cause_service", "expected_signals"}
    assert not GROUND_TRUTH_FIELDS & property_names(committed("controller"))
    assert not GROUND_TRUTH_FIELDS & property_names(committed("gateway"))


def test_export_check_reports_stale_snapshots(tmp_path, capsys):
    assert export_openapi.main(["--check", "--out", str(tmp_path)]) == 1
    assert "gateway.json" in capsys.readouterr().err
    assert export_openapi.main(["--out", str(tmp_path)]) == 0
    assert export_openapi.main(["--check", "--out", str(tmp_path)]) == 0
    (tmp_path / "controller.json").write_text("{}\n", encoding="utf-8")
    assert export_openapi.main(["--check", "--out", str(tmp_path)]) == 1


async def test_ui_config_matches_the_shared_web_contract(mesh, tmp_path):
    contract = json.loads((WEB / "contracts" / "ui-config.json").read_text(encoding="utf-8"))
    store = (await mesh["client"].get("/ui/config")).json()
    assert store["mode"] == "store"
    assert shape(store) == shape(contract)

    app = controller_app(
        path=tmp_path / "incidents.jsonl",
        client=httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200))),
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://127.0.0.1:8090") as client:
        control = (await client.get("/ui/config")).json()
    assert control["mode"] == "control"
    assert shape(control) == shape(contract)
