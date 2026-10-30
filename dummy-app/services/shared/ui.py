"""Local, same-origin interfaces; evaluation controls stay on the controller only."""

import os
import re
from pathlib import Path, PurePosixPath

from fastapi import Request
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

ASSETS = Path(__file__).resolve().parents[1] / "ui" / "static"


def register_ui(app, mode):
    """/ui/config for the page's own origin. The legacy Arabic UI remains only for the operator
    (the controller's /); the store is the React bundle at / since P4-H (register_store_pages)."""
    if mode not in ("store", "control"):
        raise ValueError("Unknown interface")

    if mode == "control":

        @app.get("/", include_in_schema=False)
        async def index():
            return FileResponse(ASSETS.parent / "control.html", headers={"Cache-Control": "no-cache"})

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

    if mode == "control":
        app.mount("/ui/assets", StaticFiles(directory=ASSETS), name="ui-assets")


# React bundles built from web/ (one per origin). Their files are served under /ui/next/ (the
# Vite base). The operator's pages are there too until P5; the store's pages are at / (P4-H).
# The telemetry middleware skips every /ui/ path and, on the Gateway only, the store pages.
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


# The storefront's pages on the Gateway: an explicit allowlist, never a catch-all, so an API
# error, the traffic generator's /unavailable or a typo stays an instrumented 404.
# References: services/shared/checkout.py (attempt) and shared/attempts.py (order number).
STORE_PAGE = re.compile(r"/(?:checkout|orders(?:/(?:OBS-[0-9]+-[0-9A-F]{4}|ATT-[0-9A-F]{4}-[0-9]{4}))?)?")
PAGE_METHODS = ("GET", "HEAD")


def is_store_page(path):
    """True for exactly the store's page paths: /, /checkout, /orders and /orders/<ref>."""
    return STORE_PAGE.fullmatch(path) is not None


def is_untraced_page(app, method, path):
    """A page request this app serves (GET or HEAD on its page allowlist): not business
    telemetry. Only an app that registered pages has any, so the orders service's own /orders
    API is never affected."""
    pages = getattr(app.state, "page_paths", None)
    return pages is not None and method in PAGE_METHODS and pages(path)


def _entry_page(entry):
    return FileResponse(
        entry,
        media_type="text/html; charset=utf-8",
        headers={"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"},
    )


def register_store_pages(app):
    """The store's entry page at each allowlisted page path, for GET and HEAD; a plain 404
    while the bundle is not built. Not part of the API schema."""
    app.state.page_paths = is_store_page

    async def page(request: Request):
        # /orders/{ref} matches any one segment: only a real reference is a page. The raw path is
        # checked (the one the telemetry middleware sees): request.url drops tabs and newlines.
        entry = bundle_dir("store") / "index.html"
        if not is_store_page(request.scope["path"]) or not entry.is_file():
            return _not_found()
        return _entry_page(entry)

    for path in ("/", "/checkout", "/orders", "/orders/{ref}"):
        app.add_api_route(path, page, methods=list(PAGE_METHODS), include_in_schema=False)


def register_bundle(app, name, *, pages_at_root=False):
    """Serve one built bundle's files at /ui/next/: hashed files under assets/ and licence texts
    under licenses/. Its pages are at /ui/next/ too, unless ``pages_at_root`` (the store since
    P4-H), where an old /ui/next/<page> link redirects to /<page>. Anything else, or any request
    while the bundle is not built, is a plain 404."""
    if name not in BUNDLES:
        raise ValueError("Unknown bundle")

    def redirect(target, query):
        return RedirectResponse(target + (f"?{query}" if query else ""), status_code=308)

    @app.api_route(BUNDLE_PATH, methods=["GET", "HEAD"], include_in_schema=False)
    async def bundle_root(request: Request):
        return redirect("/" if pages_at_root else f"{BUNDLE_PATH}/", request.url.query)

    @app.api_route(BUNDLE_PATH + "/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    async def bundle_file(path: str, request: Request):
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
        if pages_at_root:
            page = "/" + path
            return redirect(page, request.url.query) if is_store_page(page) else _not_found()
        return _entry_page(entry)
