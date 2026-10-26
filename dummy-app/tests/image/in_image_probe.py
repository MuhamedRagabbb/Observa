"""Runs INSIDE the built image (piped to `python -` by test_image.py) and prints one JSON report.

It drives the real Gateway and controller apps in-process through httpx's ASGI transport, so it
needs no network, database or other container. It never writes outside a temporary folder.
"""

import asyncio
import json
import os
import pwd
import shutil
import tempfile
from pathlib import Path

import httpx

from controllers.fault_injector.app import create_app as controller_app
from services.gateway.app import create_app as gateway_app
from services.shared.config import Settings

DIST = Path("services/ui/dist")


def scripts(name):
    return sorted(p.name for p in (DIST / name / "assets").glob("*.js"))


def node_modules_dirs():
    found = []
    for root in ("/app", "/usr", "/opt", "/home", "/root", "/tmp"):
        if os.access(root, os.R_OK):
            for current, dirs, _ in os.walk(root):
                if "node_modules" in dirs:
                    found.append(os.path.join(current, "node_modules"))
                dirs[:] = [d for d in dirs if d != "proc"]
    return found


async def requests(app, paths, *, lifespan=False):
    async def run():
        out = {}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://probe") as client:
            for path in paths:
                response = await client.get(path, follow_redirects=False)
                out[path] = {
                    "status": response.status_code,
                    "cache": response.headers.get("cache-control"),
                    "type": response.headers.get("content-type"),
                    "trace": "x-trace-id" in response.headers,
                    "title": response.text.split("<title>")[1].split("</title>")[0]
                    if "<title>" in response.text
                    else None,
                    "arabic": 'lang="ar" dir="rtl"' in response.text,
                }
        return out

    if not lifespan:
        return await run()
    async with app.router.lifespan_context(app):
        return await run()


async def main():
    store_js, operator_js = scripts("store"), scripts("operator")
    fonts = sorted(p.name for p in (DIST / "store" / "assets").glob("*.woff2"))
    common = ["/", "/ui/next", "/ui/next/", "/ui/next/orders/ATT-1", "/ui/next/missing.js"]
    gateway_paths = common + [f"/ui/next/assets/{n}" for n in store_js + operator_js + fonts[:1]]
    gateway_paths += ["/ui/next/licenses/inter-OFL.txt", "/api/products"]
    controller_paths = common + [f"/ui/next/assets/{n}" for n in operator_js + store_js]

    gateway = gateway_app(Settings(otlp_endpoint=""))
    with tempfile.TemporaryDirectory() as scratch:
        targets = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200)))
        controller = controller_app(path=Path(scratch) / "incidents.jsonl", client=targets, health=None)
        report = {
            "uid": os.getuid(),
            "user": pwd.getpwuid(os.getuid()).pw_name,
            "cwd": os.getcwd(),
            "node": shutil.which("node"),
            "npm": shutil.which("npm"),
            "npx": shutil.which("npx"),
            "node_modules": node_modules_dirs(),
            "web_sources": Path("web").exists(),
            "bytecode": sorted(str(p) for p in Path("/app").rglob("*.pyc")),
            "dist": {
                name: sorted(str(p.relative_to(DIST / name)) for p in (DIST / name).rglob("*") if p.is_file())
                for name in ("store", "operator")
            },
            "dist_writable": os.access(DIST / "store" / "index.html", os.W_OK),
            "dist_readable": os.access(DIST / "store" / "index.html", os.R_OK),
            "scripts": {"store": store_js, "operator": operator_js},
            "gateway": await requests(gateway, gateway_paths),
            "controller": await requests(controller, controller_paths, lifespan=True),
            "openapi": {"gateway": gateway.openapi(), "controller": controller.openapi()},
        }
    print(json.dumps(report))


asyncio.run(main())
