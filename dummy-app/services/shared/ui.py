"""Local, same-origin interfaces; evaluation controls stay on the controller only."""

import os
from pathlib import Path

from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

ASSETS = Path(__file__).resolve().parents[1] / "ui" / "static"


def register_ui(app, mode):
    if mode not in ("store", "control"):
        raise ValueError("Unknown interface")

    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(ASSETS.parent / f"{mode}.html", headers={"Cache-Control": "no-cache"})

    @app.get("/ui/config", include_in_schema=False)
    async def config():
        return {
            "mode": mode,
            "ports": {
                "store": int(os.getenv("PUBLIC_GATEWAY_PORT", "8000")),
                "control": int(os.getenv("PUBLIC_CONTROLLER_PORT", "8090")),
                "grafana": int(os.getenv("PUBLIC_GRAFANA_PORT", "3000")),
                "jaeger": int(os.getenv("PUBLIC_JAEGER_PORT", "16686")),
                "prometheus": int(os.getenv("PUBLIC_PROMETHEUS_PORT", "9090")),
            },
        }

    app.mount("/ui/assets", StaticFiles(directory=ASSETS), name="ui-assets")
