import importlib
import logging
import os

import uvicorn

from services.shared.telemetry import JsonFormatter


def main():
    service = os.environ["SERVICE"]
    if service not in ("gateway", "orders", "inventory", "payments", "controller"):
        raise ValueError("Unknown service")
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter(service, os.getenv("ENVIRONMENT", "local")))
    logging.basicConfig(handlers=[handler], level=logging.WARNING, force=True)
    module = "controllers.fault_injector.app" if service == "controller" else f"services.{service}.app"
    app = importlib.import_module(module).create_app()
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.environ["PORT"]),
        access_log=False,
        log_config=None,
        timeout_graceful_shutdown=15,
        limit_concurrency=64,
    )


if __name__ == "__main__":
    main()
