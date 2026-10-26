"""Local, same-origin interfaces; evaluation controls stay on the controller only."""

import os
from pathlib import Path, PurePosixPath

from fastapi import Request
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse
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


# React bundles built from web/ (one per origin). They are served under /ui/next/ while the
# legacy UI above keeps /. The telemetry middleware skips every /ui/ path.
BUNDLES = ("store", "operator")
BUNDLE_PATH = "/ui/next"
DEFAULT_BUNDLE_DIR = Path(__file__).resolve().parents[1] / "ui" / "dist"
IMMUTABLE = "public, max-age=31536000, immutable"
MEDIA_TYPES = {
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".woff2": "font/woff2",
    ".txt": "text/plain; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".webp": "image/webp",
    ".ico": "image/x-icon",
    ".json": "application/json",
    ".map": "application/json",
}
# Hashed build output, safe to cache for a year; licence texts keep stable names.
FILE_FOLDERS = {"assets": IMMUTABLE, "licenses": "no-cache"}


def bundle_dir(name):
    """Where a bundle's build lives: UI_BUNDLE_DIR/<name>, read per request."""
    return Path(os.getenv("UI_BUNDLE_DIR") or DEFAULT_BUNDLE_DIR) / name


def _not_found():
    return PlainTextResponse("Not found", status_code=404, headers={"Cache-Control": "no-cache"})


def _file_in(folder, relative):
    """The file at ``relative`` inside ``folder``, or None if it is missing or outside it.
    The resolved name must match the requested one exactly, so a case-insensitive file
    system (Windows) cannot serve one file under several URLs ("A.JS", "a.js.")."""
    try:
        candidate = (folder / relative).resolve()
        if (
            candidate.is_relative_to(folder.resolve())
            and candidate.is_file()
            and candidate.name == relative.rsplit("/", 1)[-1]
        ):
            return candidate
    except (OSError, ValueError):
        pass
    return None


def register_bundle(app, name):
    """Serve one built bundle at /ui/next/: the entry page for the root and client-side
    routes, hashed files under assets/ and licence texts under licenses/. Anything else,
    or any request while the bundle is not built, is a plain 404."""
    if name not in BUNDLES:
        raise ValueError("Unknown bundle")

    @app.api_route(BUNDLE_PATH, methods=["GET", "HEAD"], include_in_schema=False)
    async def bundle_root(request: Request):
        query = request.url.query
        return RedirectResponse(f"{BUNDLE_PATH}/" + (f"?{query}" if query else ""), status_code=308)

    @app.api_route(BUNDLE_PATH + "/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    async def bundle_file(path: str):
        build = bundle_dir(name)
        entry = build / "index.html"
        if not entry.is_file():
            return _not_found()
        folder, _, rest = path.partition("/")
        if folder in FILE_FOLDERS:
            file = _file_in(build / folder, rest) if rest else None
            if file is None:
                return _not_found()
            return FileResponse(
                file,
                media_type=MEDIA_TYPES.get(file.suffix, "application/octet-stream"),
                headers={"Cache-Control": FILE_FOLDERS[folder], "X-Content-Type-Options": "nosniff"},
            )
        if "." in PurePosixPath(path).name:
            return _not_found()  # a file request outside assets/ is never a client-side route
        return FileResponse(
            entry,
            media_type="text/html; charset=utf-8",
            headers={"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"},
        )
