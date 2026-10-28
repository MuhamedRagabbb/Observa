"""Which browser requests the controller answers (P5-0).

The controller has no login: it is published on 127.0.0.1 only and trusted on that machine. A
browser on the same machine is the gap: any web page could send it a form or fetch, and DNS
rebinding can make a hostile name resolve to 127.0.0.1, which lets that page read the answers too
(the active scenario, the activity log). So

- every request must name a loopback host in its ``Host`` header (127.0.0.1, localhost or
  [::1], any port): a rebound hostile name is refused, for reads as well as changes;
- a state-changing request (anything but exactly GET, HEAD or OPTIONS) that carries ``Origin``
  (every browser does on POST) must come from exactly this host's own origin (``http://<Host>``;
  ``null`` and any other scheme, host or port are refused), and one that carries
  ``Sec-Fetch-Site`` must say ``same-origin`` or ``none``.

Requests without ``Origin`` (the CLI, scripts/faults.py, test clients) only need the loopback
Host. Reads skip the Origin check: they change nothing, and a cross-origin page cannot read the
answer (the controller sends no CORS headers). Only the raw ``Host``, ``Origin`` and
``Sec-Fetch-Site`` headers count: ``X-Forwarded-*`` and ``Forwarded`` are never read, and two
copies of any of them are refused as ambiguous.

This stops browsers, not other programs: a process or container that can reach the controller's
port can send any Host it likes. The network is the boundary for those (the controller is
published on 127.0.0.1 only, and only trusted containers share its Docker networks).
"""

import json
import re
from uuid import uuid4

from services.shared.telemetry import utcnow

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
ALLOWED_FETCH_SITES = frozenset({"same-origin", "none"})
# host[:port] or [ipv6][:port]; nothing else (no user info, spaces, paths or trailing dots).
HOST = re.compile(r"(?:\[(?P<ipv6>[0-9A-Fa-f:]+)\]|(?P<name>[A-Za-z0-9.-]+))(?::(?P<port>[0-9]{1,5}))?")
MESSAGE = "Control requests are accepted only from the controller's own page"


def _single(raw_headers, name):
    """The one value of a header, None when absent, or False when sent more than once."""
    values = [value for key, value in raw_headers if key.lower() == name]
    if len(values) > 1:
        return False
    return values[0].decode("latin-1") if values else None


def _loopback(host):
    match = HOST.fullmatch(host)
    if not match or (match["port"] and int(match["port"]) > 65535):
        return False
    hostname = (match["ipv6"] or match["name"]).lower()
    return hostname in LOOPBACK_HOSTS


def rejection(method, raw_headers):
    """Why a request must be refused (an error code), or None if it may proceed."""
    host = _single(raw_headers, b"host")
    if not host or not _loopback(host):
        return "host_not_allowed"
    if method in SAFE_METHODS:  # exactly: "get" or "Get" is routed nowhere, and checked in full
        return None
    origin = _single(raw_headers, b"origin")
    if origin is False or (origin is not None and origin != f"http://{host.lower()}"):
        return "origin_not_allowed"
    fetch_site = _single(raw_headers, b"sec-fetch-site")
    if fetch_site is False or (fetch_site is not None and fetch_site not in ALLOWED_FETCH_SITES):
        return "cross_site"
    return None


class ControlRequestGuard:
    """ASGI middleware: refuses a request (see the module docstring) before routing, validation or any effect."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        reason = rejection(scope["method"], scope["headers"]) if scope["type"] == "http" else None
        if reason is None:
            await self.app(scope, receive, send)
            return
        # The reason and path only: header values come from the sender and are never logged.
        print(
            json.dumps(
                {
                    "timestamp": utcnow(),
                    "level": "WARNING",
                    "service.name": "fault-controller",
                    "event": "control_request_refused",
                    "message": "control_request_refused",
                    "request_id": "system",
                    "reason": reason,
                    "method": scope["method"],
                    "path": scope["path"] if scope["path"].isascii() and scope["path"].isprintable() else "-",
                }
            ),
            flush=True,
        )
        body = json.dumps({"error_code": reason, "message": MESSAGE, "request_id": f"ctl-{uuid4()}"}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 403,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
