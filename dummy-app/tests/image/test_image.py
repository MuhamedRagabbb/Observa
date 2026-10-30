"""Checks the built image (P3-D). Opt-in, because it needs Docker and a built image:

    docker build -t observa-dummy:p3-verify .
    RUN_IMAGE_TESTS=1 python -m pytest -q tests/image

IMAGE_UNDER_TEST overrides the tag. These tests only read the image and start throwaway
containers with no network (`--network none`), removed afterwards. They never build, tag or
remove an image and never touch the Compose project.
"""

import json
import os
import subprocess
import time
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.getenv("RUN_IMAGE_TESTS") != "1", reason="set RUN_IMAGE_TESTS=1")

IMAGE = os.getenv("IMAGE_UNDER_TEST", "observa-dummy:p3-verify")
ROOT = Path(__file__).resolve().parents[2]
IMMUTABLE = "public, max-age=31536000, immutable"


def docker(*args, input=None, timeout=120):
    result = subprocess.run(
        ["docker", *args], input=input, capture_output=True, text=True, encoding="utf-8", timeout=timeout
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.fixture(scope="module")
def image():
    return json.loads(docker("image", "inspect", IMAGE))[0]


@pytest.fixture(scope="module")
def probe(image):
    script = (Path(__file__).parent / "in_image_probe.py").read_text("utf-8")
    output = docker("run", "--rm", "-i", "--network", "none", IMAGE, "python", "-", input=script, timeout=180)
    return json.loads(output.strip().splitlines()[-1])


def test_runtime_keeps_the_existing_user_entrypoint_and_base(image):
    config = image["Config"]
    assert config["User"] == "app"
    assert config["Cmd"] == ["python", "-m", "services.run"]
    assert config["WorkingDir"] == "/app"
    assert not config.get("Entrypoint")
    assert "PYTHONDONTWRITEBYTECODE=1" in config["Env"]
    assert any(env.startswith("PYTHON_VERSION=3.12.8") for env in config["Env"])


def test_runs_as_the_non_root_app_user(probe):
    assert (probe["uid"], probe["user"], probe["cwd"]) == (10001, "app", "/app")


def test_ships_no_node_runtime_or_frontend_sources(probe):
    assert (probe["node"], probe["npm"], probe["npx"]) == (None, None, None)
    assert probe["node_modules"] == []
    assert probe["web_sources"] is False


def test_ships_no_host_build_leftovers(probe):
    # Bytecode compiled on the build host must not ride along (PYTHONDONTWRITEBYTECODE=1 at runtime).
    assert probe["bytecode"] == []


def test_ships_both_production_builds_read_only(probe):
    for name in ("store", "operator"):
        files = probe["dist"][name]
        assert "index.html" in files
        assert len(probe["scripts"][name]) == 1
        assert {
            f"licenses/{family}-OFL.txt" for family in ("instrument-serif", "inter", "jetbrains-mono")
        } <= set(files)
        # Instrument Serif regular and italic (P4-B), Inter, JetBrains Mono.
        assert sum(f.endswith(".woff2") for f in files) == 4
        assert not [f for f in files if f.endswith((".map", ".ts", ".tsx"))]
    assert probe["scripts"]["store"] != probe["scripts"]["operator"]
    assert probe["dist_readable"] is True
    assert probe["dist_writable"] is False


def test_gateway_serves_only_the_store_build(probe):
    gateway, (store_js,), (operator_js,) = (
        probe["gateway"],
        probe["scripts"]["store"],
        probe["scripts"]["operator"],
    )
    # P4-H: the store's pages are at /; old /ui/next/ page links redirect; non-pages are 404.
    for path in ("/", "/checkout", "/orders", "/orders/ATT-5A0E-0001"):
        assert (gateway[path]["status"], gateway[path]["title"], gateway[path]["cache"]) == (
            200,
            "OBSERVA",
            "no-cache",
        ), path
    for path in ("/orders/ATT-1", "/unavailable", "/ui/next/orders/ATT-1"):
        assert gateway[path]["status"] == 404, path
    assert gateway["/ui/next/"]["status"] == 308
    asset = gateway[f"/ui/next/assets/{store_js}"]
    assert (asset["status"], asset["cache"]) == (200, IMMUTABLE)
    assert asset["type"].startswith("text/javascript")
    assert gateway[f"/ui/next/assets/{operator_js}"]["status"] == 404
    assert gateway["/ui/next/missing.js"]["status"] == 404
    assert gateway["/ui/next"]["status"] == 308
    font = next(v for k, v in gateway.items() if k.endswith(".woff2"))
    assert (font["status"], font["type"], font["cache"]) == (200, "font/woff2", IMMUTABLE)
    assert gateway["/ui/next/licenses/inter-OFL.txt"]["status"] == 200


def test_controller_serves_only_the_operator_build(probe):
    controller, (store_js,), (operator_js,) = (
        probe["controller"],
        probe["scripts"]["store"],
        probe["scripts"]["operator"],
    )
    page = controller["/"]
    assert (page["status"], page["title"], page["cache"]) == (200, "OBSERVA Operator", "no-cache")
    for path in ("/ui/next", "/ui/next/"):
        assert (controller[path]["status"], controller[path]["location"]) == (308, "/"), path
    for path in ("/ui/next/orders/ATT-1", "/ui/next/missing.js"):
        assert controller[path]["status"] == 404, path
    for name in ("app.js", "common.js", "control.js", "style.css", "icons.svg", "mark.svg"):
        assert controller[f"/ui/assets/{name}"]["status"] == 404, name
    assert (
        controller[f"/ui/next/assets/{operator_js}"]["status"],
        controller[f"/ui/next/assets/{store_js}"]["status"],
    ) == (
        200,
        404,
    )


def test_both_roots_serve_the_english_react_pages(probe):
    # P5-G1: both app roots serve React; the legacy Arabic operator files remain unserved.
    gateway, controller = probe["gateway"]["/"], probe["controller"]["/"]
    assert (gateway["status"], gateway["title"], gateway["arabic"]) == (200, "OBSERVA", False)
    assert (controller["status"], controller["arabic"]) == (200, False)


def test_bundle_requests_carry_no_trace_but_api_calls_do(probe):
    gateway = probe["gateway"]
    assert not [path for path, result in gateway.items() if path.startswith("/ui/") and result["trace"]]
    assert gateway["/api/products"]["trace"] is True


def test_image_api_contracts_equal_the_committed_snapshots(probe):
    for name in ("gateway", "controller"):
        committed = json.loads((ROOT / "web" / "openapi" / f"{name}.json").read_text("utf-8"))
        assert probe["openapi"][name] == committed, (
            f"{name} OpenAPI in the image differs from web/openapi/{name}.json"
        )


# Runs inside the container: fetches each path from the server on loopback, prints JSON.
FETCH = """
import json, sys, httpx
out = {}
for path in sys.argv[1:]:
    r = httpx.get("http://127.0.0.1:8000" + path, timeout=5)
    out[path] = {"status": r.status_code, "headers": dict(r.headers), "text": r.text[:4000]}
print(json.dumps(out))
"""


def test_a_gateway_container_serves_the_store_over_http():
    """The real entrypoint (python -m services.run) and uvicorn, in a container with no
    network at all: requests are made from inside it, over its own loopback."""
    name = f"p3-verify-gateway-{uuid.uuid4().hex[:8]}"
    docker(
        "run",
        "-d",
        "--rm",
        "--network",
        "none",
        "--name",
        name,
        "-e",
        "SERVICE=gateway",
        "-e",
        "PORT=8000",
        IMAGE,
    )
    try:
        for _ in range(60):
            ready = subprocess.run(
                ["docker", "exec", name, "python", "scripts/healthcheck.py"], capture_output=True
            )
            if ready.returncode == 0:
                break
            time.sleep(0.5)
        else:
            pytest.fail("gateway container never became healthy:\n" + docker("logs", name))

        def fetch(*paths):
            return json.loads(docker("exec", name, "python", "-c", FETCH, *paths))

        page = fetch("/", "/ui/next/")
        entry, old_link = page["/"], page["/ui/next/"]
        assert entry["status"] == 200 and "<title>OBSERVA</title>" in entry["text"]
        assert entry["headers"]["cache-control"] == "no-cache"
        assert '<html lang="en" dir="ltr">' in entry["text"]
        assert (old_link["status"], old_link["headers"]["location"]) == (308, "/")
        script = entry["text"].split('src="')[1].split('"')[0]
        asset = fetch(script)[script]
        assert asset["status"] == 200 and asset["headers"]["cache-control"] == IMMUTABLE
        assert "x-trace-id" not in asset["headers"]

        # PID 1 (and its children) run as uid 10001; parse the UID column, not the whole text.
        rows = [line.split(None, 2) for line in docker("top", name, "-o", "pid,uid,args").splitlines()[1:]]
        assert rows and {uid for _, uid, _ in rows} == {"10001"}
        assert any("services.run" in args for _, _, args in rows)
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
