"""A local stand-in for the F6/F7 review service read API, for tests only.

It serves ``FakeReviewService`` data over HTTP on 127.0.0.1 with the same paths as the real
service (``/api/v1/reports/{id}/versions/{v}`` and ``/api/v1/reports/{id}/review``).
"""

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from observa.f5_memory.validation import MemoryOperationError

VERSION_PATH = re.compile(r"^/api/v1/reports/([^/]+)/versions/(\d+)$")
REVIEW_PATH = re.compile(r"^/api/v1/reports/([^/]+)/review$")


class ReviewServer:
    def __init__(self, fake, token=None):
        self.fake, self.token = fake, token
        self.requests = []
        self.force_status = None
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, status, body):
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):  # noqa: N802 - http.server naming
                server.requests.append({"method": "GET", "path": self.path,
                                        "authorization": self.headers.get("Authorization")})
                if server.force_status:
                    return self._send(server.force_status, {"code": "X", "message": "forced"})
                if server.token and self.headers.get("Authorization") != f"Bearer {server.token}":
                    return self._send(401, {"code": "NOT_AUTHORIZED", "message": "token required"})
                try:
                    if match := VERSION_PATH.match(self.path):
                        return self._send(200, server.fake.report_version(match[1], int(match[2])))
                    if match := REVIEW_PATH.match(self.path):
                        return self._send(200, server.fake.review(match[1]))
                except MemoryOperationError as exc:
                    return self._send(404, {"code": exc.code, "message": str(exc)})
                return self._send(404, {"code": "NOT_FOUND", "message": "no such path"})

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._httpd.server_address[1]}"
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._httpd.shutdown()
        self._httpd.server_close()
