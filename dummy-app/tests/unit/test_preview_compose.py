"""The storefront preview project (docker-compose.preview.yml) must be unable to touch the live
stack: its own project name, image tag, host ports and ground-truth folder.

Uses `docker compose config` only: it resolves the files and starts nothing.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
# Host ports the live project uses: docker-compose.yml defaults, plus whatever .env publishes.
DEFAULT_LIVE_PORTS = {
    "8000",
    "8001",
    "8002",
    "8003",
    "8090",
    "5432",
    "9090",
    "16686",
    "3100",
    "3000",
    "4317",
    "4318",
}

pytestmark = pytest.mark.skipif(shutil.which("docker") is None, reason="needs the docker CLI")


def resolve(*files, profiles=()):
    # A clean environment: no COMPOSE_PROJECT_NAME or port overrides from the caller's shell.
    env = {k: v for k, v in os.environ.items() if not k.startswith(("COMPOSE_", "P4_"))}
    args = ["docker", "compose"]
    for name in files:
        args += ["-f", str(ROOT / name)]
    for profile in profiles:
        args += ["--profile", profile]
    result = subprocess.run(
        [*args, "config", "--format", "json"], capture_output=True, text=True, env=env, cwd=ROOT, timeout=60
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def published(config):
    return [
        (name, str(port["published"]), port.get("host_ip"))
        for name, svc in config["services"].items()
        for port in svc.get("ports", [])
    ]


@pytest.fixture(scope="module")
def preview():
    return resolve("docker-compose.yml", "docker-compose.preview.yml", profiles=("ui", "traffic"))


@pytest.fixture(scope="module")
def live_ports():
    live = {port for _, port, _ in published(resolve("docker-compose.yml", profiles=("ui",)))}
    return live | DEFAULT_LIVE_PORTS


def test_preview_is_its_own_project(preview):
    assert preview["name"] == "observa-p4-preview"
    for network in preview["networks"].values():
        assert network["name"].startswith("observa-p4-preview_")
    for volume in preview["volumes"].values():
        assert volume["name"].startswith("observa-p4-preview_")


def test_every_built_service_uses_the_preview_tag_never_local(preview):
    built = {name: svc for name, svc in preview["services"].items() if "build" in svc}
    assert {"gateway", "orders", "inventory", "payments", "fault-injector", "traffic-generator"} <= set(built)
    assert {svc["image"] for svc in built.values()} == {"observa-dummy:p4-preview"}
    assert not [
        name for name, svc in preview["services"].items() if svc.get("image") == "observa-dummy:local"
    ]


def test_no_preview_port_collides_with_the_live_stack(preview, live_ports):
    ports = published(preview)
    assert ports, "the preview must publish its gateway and controller"
    assert not [p for p in ports if p[1] in live_ports]
    assert {p[2] for p in ports} == {"127.0.0.1"}
    assert ("gateway", "18000", "127.0.0.1") in ports
    assert ("fault-injector", "18090", "127.0.0.1") in ports


def test_background_traffic_only_starts_on_request():
    plain = resolve("docker-compose.yml", "docker-compose.preview.yml")
    assert "traffic-generator" not in plain["services"]


def test_preview_services_never_restart_by_themselves(preview):
    assert {
        name: svc.get("restart") for name, svc in preview["services"].items() if svc.get("restart") != "no"
    } == {}


def test_ui_links_follow_the_preview_ports(preview):
    for name in ("gateway", "fault-injector"):
        env = preview["services"][name]["environment"]
        assert (env["PUBLIC_GATEWAY_PORT"], env["PUBLIC_CONTROLLER_PORT"]) == ("18000", "18090")


def test_the_preview_journal_is_not_the_live_ground_truth(preview):
    mounts = [m for m in preview["services"]["fault-injector"]["volumes"] if m["target"] == "/ground-truth"]
    assert len(mounts) == 1
    source = Path(mounts[0]["source"])
    assert source != ROOT / "ground-truth"
    assert source == ROOT / ".preview" / "ground-truth"


def test_the_live_compose_file_alone_is_unchanged():
    live = resolve("docker-compose.yml")
    assert live["name"] == "observa-dummy"
    assert {svc.get("image") for svc in live["services"].values() if "build" in svc} == {
        "observa-dummy:local"
    }
