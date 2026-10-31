"""Explicitly isolated, bounded local controller harness. Importing this module starts nothing.

Exit codes: 0 clean, 2 refused, 3 start failure, 4 forced, 5 shutdown failed.
"""

import argparse
import asyncio
import gzip
import json
import os
import re
import secrets
import signal
import sys
import zlib
from collections import OrderedDict, deque
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qs, unquote

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from controllers.fault_injector.health import HEALTH_SERVICES
from services.shared.telemetry import utcnow

HOST = "127.0.0.1"
ROOT = Path(__file__).resolve().parents[2]
JOURNAL_ROOT = ROOT / "web" / "e2e-output"
JOURNAL_ANCHOR = ROOT.resolve() / "web" / "e2e-output"
JOURNAL_DIR = JOURNAL_ROOT / "contract-journal"
_PORTS = json.loads((ROOT / "web" / "contracts" / "stack-ports.json").read_text("utf-8"))
LIVE_PORTS = frozenset(_PORTS["live"])
PREVIEW_PORTS = frozenset(_PORTS["preview"].values())
MAX_CALLS = 500
MAX_NONCES = 16
STALL_SECONDS = 5.0
HOLD_LIMIT_SECONDS = 2.5
# The ground-truth probe's own hold limit: the fixture self-tests need real headroom to observe a held request
# and act on it, but a forgotten hold must still end well inside SHUTDOWN_TIMEOUT.
PROBE_HOLD_LIMIT_SECONDS = 6.0
STARTUP_TIMEOUT = 30
SHUTDOWN_TIMEOUT = 10


class UnsafeHarness(Exception):
    """Settings cannot guarantee isolation from the live and preview stacks."""


@dataclass(frozen=True)
class HarnessConfig:
    controller_port: int
    control_port: int
    web_dist: Path
    max_seconds: int


def _decimal(name, value, low, high):
    if (
        not isinstance(value, str)
        or not value.isascii()
        or not re.fullmatch(r"[1-9][0-9]*", value)
        or len(value) > len(str(high))
        or not low <= int(value) <= high
    ):
        raise UnsafeHarness(f"{name} must be an ASCII decimal integer in {low}..{high}")
    return int(value)


def validate_isolation(env, *, web_dist):
    """Validate without changing the environment or constructing a controller/server."""
    forbidden = {
        "PROMETHEUS_URL", "ORDERS_CONTROL_URL", "PAYMENTS_CONTROL_URL", "GROUND_TRUTH_PATH",
        "UI_BUNDLE_DIR", "HEALTH_INTERVAL_SECONDS",
    }
    for name in env:
        if name.upper() in {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"} and env[name]:
            raise UnsafeHarness(f"{name} proxy must be absent or empty in the harness environment")
        if name in forbidden or name.startswith(("PUBLIC_", "OTEL_EXPORTER")):
            raise UnsafeHarness(f"{name} must be absent from the harness environment")
    ports = []
    for name in ("CONTRACT_CONTROLLER_PORT", "CONTRACT_CONTROL_PORT"):
        port = _decimal(name, env.get(name), 1024, 65535)
        if port in LIVE_PORTS:
            raise UnsafeHarness(f"{name} is a live stack port")
        if port in PREVIEW_PORTS:
            raise UnsafeHarness(f"{name} is a preview project port")
        ports.append(port)
    if ports[0] == ports[1]:
        raise UnsafeHarness("The controller and control ports must be different")
    lifetime = _decimal("CONTRACT_MAX_SECONDS", env.get("CONTRACT_MAX_SECONDS", "1800"), 10, 3600)
    if not (web_dist / "operator" / "index.html").is_file():
        raise UnsafeHarness("Missing operator bundle; run npm run build in web first")
    return HarnessConfig(ports[0], ports[1], web_dist, lifetime)


def controller_environment(config):
    base = f"http://{HOST}:{config.control_port}"
    return {
        "ORDERS_CONTROL_URL": f"{base}/orders",
        "PAYMENTS_CONTROL_URL": f"{base}/payments",
        "UI_BUNDLE_DIR": str(config.web_dist),
        "PUBLIC_GATEWAY_PORT": str(config.control_port),
        "PUBLIC_CONTROLLER_PORT": str(config.controller_port),
        "PUBLIC_GRAFANA_PORT": str(config.control_port),
        "PUBLIC_JAEGER_PORT": str(config.control_port),
        "PUBLIC_PROMETHEUS_PORT": str(config.control_port),
    }


def apply_environment(config):
    os.environ.update(controller_environment(config))


def prepare_journal(directory=None, *, allowed_root=None):
    """Clear only regular journal files, within the resolved harness output folder."""
    try:
        root = Path(JOURNAL_ROOT if allowed_root is None else allowed_root).resolve()
        if allowed_root is None:
            anchor = Path(JOURNAL_ANCHOR)
            if root != anchor:
                raise UnsafeHarness("The journal output root must match the repository journal anchor")
            journal_directory = Path(JOURNAL_DIR)
            relative = journal_directory.relative_to(Path(JOURNAL_ROOT))
            if (
                journal_directory.is_symlink() or journal_directory.is_junction()
                or journal_directory.resolve() != anchor.resolve() / relative
            ):
                raise UnsafeHarness("The journal directory must be unlinked at its exact anchored location")
        folder = Path(JOURNAL_DIR if directory is None else directory).resolve()
        if folder == root or not folder.is_relative_to(root):
            raise UnsafeHarness("The journal directory must be strictly inside the allowed output root")
        if folder.exists() and not folder.is_dir():
            raise UnsafeHarness("The journal directory must be a directory")
        folder.mkdir(parents=True, exist_ok=True)
        journals = [folder / name for name in ("incidents.jsonl", "incidents.tmp")]
        for journal in journals:
            if journal.is_symlink():
                raise UnsafeHarness(f"{journal.name} must be a regular file, never a symlink")
            if journal.exists() and not journal.is_file():
                raise UnsafeHarness(f"{journal.name} must be a regular file")
        for journal in journals:
            if journal.exists():
                journal.unlink()
        return folder / "incidents.jsonl"
    except (OSError, RuntimeError, ValueError) as exc:
        raise UnsafeHarness(f"Cannot safely prepare the journal: {exc}") from exc


class StubHealth:
    def __init__(self):
        self.reset()

    def reset(self):
        self.states = dict.fromkeys(HEALTH_SERVICES, "healthy")

    def set_states(self, mapping):
        allowed = {"healthy", "degraded", "unavailable", "unknown"}
        if not isinstance(mapping, Mapping) or any(
            name not in HEALTH_SERVICES or not isinstance(state, str) or state not in allowed
            for name, state in mapping.items()
        ):
            raise ValueError("Unknown harness service or health state")
        self.states.update(mapping)

    def snapshot(self):
        checked_at = utcnow()
        rows = []
        for name, url in HEALTH_SERVICES.items():
            state = self.states[name]
            unknown = state == "unknown"
            unavailable = state == "unavailable"
            rows.append({
                "service": name,
                "state": state,
                "checked_at": checked_at,
                "probe": None if url is None else {
                    "ok": not unavailable, "status_code": None if unavailable else 200, "latency_ms": 0.0,
                },
                "error_ratio": None if unknown else 0.5 if state == "degraded" else 0.0,
                "mean_latency_ms": None if unknown else 1.0,
                "p95_latency_ms": None if unknown else 1.0,
                "reasons": ["stale_metrics"] if unknown else ["harness_state"] if state == "degraded"
                else ["probe_failed"] if unavailable else [],
            })
        return {"checked_at": checked_at, "metrics": "available", "services": rows}

    async def check(self):
        return self.snapshot()


class StubState:
    def __init__(self, hold_limit=HOLD_LIMIT_SECONDS, stall=STALL_SECONDS, probe_hold_limit=PROBE_HOLD_LIMIT_SECONDS):
        self.hold_limit = hold_limit
        self.probe_hold_limit = probe_hold_limit
        self.stall = stall
        self.modes = {"orders": "ack", "payments": "ack"}
        self.calls = deque(maxlen=MAX_CALLS)
        self.requests = deque(maxlen=MAX_CALLS)
        self.redirect_nonces = OrderedDict()
        self.sentinel_hits = 0
        self.sentinel_contacts = deque(maxlen=MAX_CALLS)
        self.shutdown_hook = None
        self.health = StubHealth()
        self._release = asyncio.Event()

    def release(self):
        # A release is a generation boundary: a later request gets a fresh, unset event.
        self._release.set()
        self._release = asyncio.Event()

    def reset(self):
        self.release()
        self.modes.update(orders="ack", payments="ack")
        self.calls.clear()
        self.requests.clear()
        self.redirect_nonces.clear()
        self.sentinel_hits = 0
        self.sentinel_contacts.clear()
        self.health.reset()

    def mint_redirect_nonce(self):
        nonce = secrets.token_hex(16)
        self.redirect_nonces[nonce] = True
        if len(self.redirect_nonces) > MAX_NONCES:
            self.redirect_nonces.popitem(last=False)
        return nonce

    def consume_redirect_nonce(self, nonce):
        return self.redirect_nonces.pop(nonce, False)

    async def hold(self):
        await self._wait_for_release(self.hold_limit)

    async def probe_hold(self):
        await self._wait_for_release(self.probe_hold_limit)

    async def _wait_for_release(self, limit):
        released = self._release
        try:
            await asyncio.wait_for(released.wait(), limit)
        except TimeoutError:
            pass


class _HeldProbeResponse(Response):
    """A probe answer that waits like a held stub target: until the test releases it (or the hold limit)."""

    def __init__(self, hold, **kwargs):
        super().__init__(**kwargs)
        self._hold = hold

    async def __call__(self, scope, receive, send):
        await self._hold()
        await super().__call__(scope, receive, send)


class RecordingApp:
    """Record allowed headers before the guard; the probe and its clean/compressed/held variants exist only for the fixture's self-tests because the real controller never emits ground truth."""

    def __init__(self, app, state, control_port=None):
        self.app = app
        self.state = state
        # The redirect source may point ONLY at the sentinel on this harness control origin. A deny-list of
        # live and preview ports is incomplete (other local services exist), so nothing else is ever served;
        # without a control port it serves nothing at all.
        self.control_port = control_port
        self._redirect_target = None
        if control_port is not None:
            if (
                isinstance(control_port, bool) or not isinstance(control_port, int)
                or not 1024 <= control_port <= 65535 or control_port in LIVE_PORTS | PREVIEW_PORTS
            ):
                raise UnsafeHarness("The recorder's control port must be a harness port, never a live or preview one")
            self._redirect_target = re.compile(
                rf"http://127\.0\.0\.1:{control_port}/sentinel(?:/[A-Za-z0-9_-]+)*/?(?:[?#].*)?"
            )

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = scope.get("headers", [])

            def header(name):
                values = [value for key, value in headers if key.lower() == name]
                if len(values) > 1:
                    return "<repeated>"
                return values[0].decode("latin-1")[:200] if values else None

            self.state.requests.append({
                "method": scope["method"],
                "path": scope["path"],
                "host": header(b"host"),
                "origin": header(b"origin"),
                "sec_fetch_site": header(b"sec-fetch-site"),
                "sec_fetch_mode": header(b"sec-fetch-mode"),
                "sec_fetch_dest": header(b"sec-fetch-dest"),
                "reissued": header(b"x-contract-reissued"),
                "at": utcnow(),
            })
            if scope["path"].startswith("/__harness__/"):
                return await self._harness_response(scope)(scope, receive, send)
        await self.app(scope, receive, send)

    def _harness_response(self, scope):
        headers = scope.get("headers", [])
        hosts = [value.decode("latin-1") for key, value in headers if key.lower() == b"host"]
        if len(hosts) != 1 or not re.fullmatch(
            r"(?:127\.0\.0\.1|localhost|\[::1\])(?::[0-9]+)?", hosts[0],
        ):
            return JSONResponse({"error_code": "host_not_allowed"}, status_code=403)
        query = parse_qs(scope.get("query_string", b"").decode("latin-1"), keep_blank_values=True)
        harness_headers = [value for key, value in headers if key.lower() == b"x-contract-harness"]
        if harness_headers:
            # Even an invalid or repeated header must never consume a query nonce.
            authorized = harness_headers == [b"1"]
        else:
            nonces = query.get("nonce", [])
            authorized = (
                scope["path"] == "/__harness__/redirect"
                and len(nonces) == 1
                and self.state.consume_redirect_nonce(nonces[0])
            )
        if not authorized:
            return JSONResponse({"error_code": "harness_header_required"}, status_code=403)
        if scope["path"] == "/__harness__/ground-truth-probe":
            if scope["method"] != "GET":
                return JSONResponse({"error_code": "method_not_allowed"}, status_code=405)
            options = {
                "kind": {"leak", "invalid", "clean"},
                "encoding": {"gzip", "deflate"},
                "hold": {"0", "1"},
            }
            if any(
                name not in options or len(values) != 1 or values[0] not in options[name]
                for name, values in query.items()
            ):
                return JSONResponse({"error_code": "invalid_probe"}, status_code=400)
            kind = query.get("kind", ["leak"])[0]
            if kind == "invalid":
                body = b"{not json"
            else:
                payload = {"state": "idle", "probe": "clean"} if kind == "clean" else {
                    "state": "idle", "root_cause_service": "harness-probe",
                }
                body = JSONResponse(payload).body
            encoding = query.get("encoding", [None])[0]
            if encoding == "gzip":
                body = gzip.compress(body, mtime=0)
            elif encoding == "deflate":
                body = zlib.compress(body)
            fields = {
                "content": body, "media_type": "application/json",
                "headers": {"content-encoding": encoding} if encoding is not None else None,
            }
            if query.get("hold", ["0"])[0] == "1":
                return _HeldProbeResponse(self.state.probe_hold, **fields)
            return Response(**fields)
        if scope["path"] != "/__harness__/redirect":
            return JSONResponse({"error_code": "not_found"}, status_code=404)
        if scope["method"] != "GET":
            return JSONResponse({"error_code": "method_not_allowed"}, status_code=405)
        targets, statuses = query.get("to", []), query.get("status", ["302"])
        try:
            if len(targets) != 1 or len(statuses) != 1:
                raise ValueError("ambiguous redirect")
            target, status = targets[0], statuses[0]
            if (
                len(target) > 2048
                or any(ord(char) <= 32 or ord(char) == 127 or char == "\\" for char in target)
                or status not in {"301", "302", "303", "307", "308"}
            ):
                raise ValueError("invalid redirect")
            # Exactly the sentinel on the harness control origin, in one canonical spelling (no dot segments,
            # encodings, user info or other host spellings), so Python and Chromium cannot read it
            # differently and the harness can never send a browser to any real or unrelated service.
            if self._redirect_target is None or self._redirect_target.fullmatch(target) is None:
                raise ValueError("invalid redirect")
            target.encode("latin-1")
        except (ValueError, UnicodeError):
            return JSONResponse({"error_code": "invalid_redirect"}, status_code=400)
        return Response(status_code=int(status), headers={"location": target})


def _is_sentinel_target(path):
    """Classify absolute-form and encoded targets conservatively, without changing the logged path."""
    absolute = re.match(r"[A-Za-z][A-Za-z0-9+.-]*://[^/?#]*(/[^?#]*)?", path)
    if absolute:
        path = absolute[1] or "/"
    path = unquote(path)
    return path == "/sentinel" or path.startswith("/sentinel/")


class _SentinelLedger:
    """Count every sentinel contact outside the control app's guards and method routing."""

    def __init__(self, app, state):
        self.app = app
        self.state = state

    async def __call__(self, scope, receive, send):
        if scope["type"] in {"http", "websocket"}:
            path = scope["path"]
            if _is_sentinel_target(path):
                self.state.sentinel_hits += 1
                self.state.sentinel_contacts.append({
                    "method": "WEBSOCKET" if scope["type"] == "websocket" else scope["method"],
                    "path": path,
                    "at": utcnow(),
                })
        await self.app(scope, receive, send)


class _StubGuard:
    """Pure ASGI guard, before parsing or routing. Never emits CORS headers."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = scope.get("headers", [])
            hosts = [value.decode("latin-1") for key, value in headers if key.lower() == b"host"]
            if len(hosts) != 1 or not re.fullmatch(
                r"(?:127\.0\.0\.1|localhost|\[::1\])(?::[0-9]+)?", hosts[0],
            ):
                response = JSONResponse({"error_code": "host_not_allowed"}, status_code=403)
                return await response(scope, receive, send)
            if scope["path"].startswith("/__test__/") and (
                [value for key, value in headers if key.lower() == b"x-contract-harness"] != [b"1"]
            ):
                response = JSONResponse({"error_code": "harness_header_required"}, status_code=403)
                return await response(scope, receive, send)
        await self.app(scope, receive, send)


class _Mode(BaseModel):
    target: Literal["orders", "payments", "all"]
    mode: Literal["ack", "fail", "hold", "stall"]


class _HealthStates(BaseModel):
    states: dict[str, str]


def build_stub_app(state):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(_StubGuard)

    async def runtime(target, request):
        try:
            body = await request.json()
        except (ValueError, UnicodeDecodeError):
            body = None
        state.calls.append({"target": target, "method": request.method, "body": body, "at": utcnow()})
        mode = state.modes[target]
        if mode == "fail":
            return JSONResponse({"error_code": "stub_failure"}, status_code=500)
        if mode == "hold":
            await state.hold()
        elif mode == "stall":
            await asyncio.sleep(state.stall)
        return {"status": "cleared" if request.method == "DELETE" else "accepted"}

    @app.api_route("/orders/_control/runtime", methods=["PUT", "DELETE"])
    async def orders(request: Request):
        return await runtime("orders", request)

    @app.api_route("/payments/_control/runtime", methods=["PUT", "DELETE"])
    async def payments(request: Request):
        return await runtime("payments", request)

    @app.post("/__test__/mode")
    async def mode(body: _Mode):
        for target in state.modes if body.target == "all" else [body.target]:
            state.modes[target] = body.mode
        return {"status": "accepted"}

    @app.post("/__test__/release")
    async def release():
        state.release()
        return {"status": "released"}

    @app.post("/__test__/reset")
    async def reset():
        state.reset()
        return {"status": "reset"}

    @app.post("/__test__/shutdown")
    async def shutdown():
        if state.shutdown_hook is not None:
            state.shutdown_hook()
        return {"status": "stopping"}

    @app.post("/__test__/health")
    async def set_health(body: _HealthStates):
        try:
            state.health.set_states(body.states)
        except ValueError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=422)
        return state.health.snapshot()

    @app.get("/__test__/health")
    async def health():
        return state.health.snapshot()

    @app.get("/__test__/alive")
    async def alive():
        # Which process this is, so a stop can be proven by THAT process being gone (a closed port is not
        # evidence: uvicorn closes its listeners before the controller's shutdown has finished).
        return {"pid": os.getpid()}

    @app.get("/__test__/calls")
    async def calls():
        return {"calls": list(state.calls)}

    @app.get("/sentinel")
    @app.get("/sentinel/{path:path}")
    async def sentinel():
        return {"status": "hit"}

    @app.get("/__test__/sentinel")
    async def sentinel_hits():
        return {"hits": state.sentinel_hits}

    @app.get("/__test__/sentinel-contacts")
    async def sentinel_contacts():
        return {"contacts": list(state.sentinel_contacts)}

    @app.get("/__test__/requests")
    async def requests():
        return {"requests": list(state.requests)}

    @app.post("/__test__/redirect-nonce")
    async def redirect_nonce():
        return {"nonce": state.mint_redirect_nonce()}

    return _SentinelLedger(app, state)


def build_controller_app(config, journal, health, *, client=None):
    from controllers.fault_injector.app import create_app

    app = create_app(path=str(journal), client=client, health=health, health_interval=0.5)
    documentation = {"/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"}
    app.router.routes = [
        route for route in app.router.routes if getattr(route, "path", None) not in documentation
    ]
    return app


async def serve(config):
    try:
        journal = prepare_journal()
    except UnsafeHarness as exc:
        print(f"contract-harness refused: {exc}", file=sys.stderr)
        return 2
    apply_environment(config)
    import uvicorn

    class HarnessServer(uvicorn.Server):
        @contextmanager
        def capture_signals(self):
            # uvicorn 0.34 uses this context manager; the supervisor owns all signals.
            yield

        def install_signal_handlers(self):
            # Compatibility with uvicorn releases that used this earlier hook.
            pass

    loop = asyncio.get_running_loop()
    deadline = loop.time() + config.max_seconds
    stop = asyncio.Event()
    previous = {}
    servers, tasks = [], []
    server_failures = []
    exit_code = 0
    forced = False

    def request_stop(_signum, _frame):
        loop.call_soon_threadsafe(stop.set)

    async def run_server(server):
        try:
            await server.serve()
        except (SystemExit, Exception) as exc:
            # uvicorn uses SystemExit for a bind failure; contain it inside this task.
            server_failures.append(exc)
            print(f"contract-harness startup/server failed on {HOST}:{server.config.port}: {exc}",
                  file=sys.stderr, flush=True)

    async def start(app, port):
        server = HarnessServer(
            uvicorn.Config(app, host=HOST, port=port, log_level="warning", access_log=False),
        )
        servers.append(server)
        task = asyncio.create_task(run_server(server))
        tasks.append(task)
        while not server.started:
            if task.done():
                raise RuntimeError(f"server on {HOST}:{port} did not start (its startup failed or the port could not be bound)")
            await asyncio.sleep(0.01)

    try:
        signals = [signal.SIGINT, signal.SIGTERM]
        if sys.platform == "win32":
            signals.append(signal.SIGBREAK)
        for signum in signals:
            previous[signum] = signal.signal(signum, request_stop)
        client = None
        try:
            state = StubState()
            state.shutdown_hook = stop.set
            async with asyncio.timeout(min(STARTUP_TIMEOUT, config.max_seconds)):
                await start(build_stub_app(state), config.control_port)
                if not stop.is_set():
                    # Inherited proxy settings must never redirect a loopback control request.
                    from controllers.fault_injector import app as controller_app

                    client = controller_app.target_client(trust_env=False)
                    app = RecordingApp(
                        build_controller_app(config, journal, state.health, client=client), state,
                        control_port=config.control_port,
                    )
                    await start(app, config.controller_port)
            if not stop.is_set():
                if not all(
                    server.started and not task.done() for server, task in zip(servers, tasks, strict=True)
                ):
                    raise RuntimeError("a server stopped before harness startup completed")
                print(f"contract-harness ready controller=http://{HOST}:{config.controller_port} "
                      f"control=http://{HOST}:{config.control_port}", flush=True)
                stopped = asyncio.create_task(stop.wait())
                try:
                    done, _ = await asyncio.wait(
                        [stopped, *tasks], timeout=max(0, deadline - loop.time()),
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if done and stopped not in done:
                        raise RuntimeError("a harness server stopped unexpectedly")
                finally:
                    stopped.cancel()
                    await asyncio.gather(stopped, return_exceptions=True)
        except Exception as exc:
            print(f"contract-harness startup/server failed: {exc or 'startup timed out'}",
                  file=sys.stderr, flush=True)
            exit_code = 3
        finally:
            for server in servers:
                server.should_exit = True
            if tasks:
                _, pending = await asyncio.wait(tasks, timeout=SHUTDOWN_TIMEOUT)
                for server, task in zip(servers, tasks, strict=True):
                    if task in pending:
                        forced = True
                        server.force_exit = True
                        task.cancel()
                        for listener in getattr(server, "servers", []):
                            listener.close()
                await asyncio.gather(*tasks, return_exceptions=True)
                forced = forced or any(server.force_exit for server in servers) or any(
                    task.cancelled() for task in tasks
                )
            if client is not None:
                await client.aclose()
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
    if exit_code:
        return exit_code
    if forced:
        print("contract-harness stopped by force", file=sys.stderr, flush=True)
        return 4
    if server_failures or any(
        getattr(getattr(server, "lifespan", None), "shutdown_failed", False) for server in servers
    ):
        reason = "lifespan shutdown failed"
        if server_failures:
            reason = (str(server_failures[0]) or type(server_failures[0]).__name__)[:200]
        print(f"contract-harness shutdown failed: {reason}", file=sys.stderr, flush=True)
        return 5
    print("contract-harness stopped cleanly", flush=True)
    return 0


def main(argv=None, *, env=None, web_dist=None):
    argparse.ArgumentParser(description=__doc__, allow_abbrev=False).parse_args(argv)
    try:
        config = validate_isolation(
            dict(os.environ) if env is None else env,
            web_dist=ROOT / "web" / "dist" if web_dist is None else web_dist,
        )
    except UnsafeHarness as exc:
        print(f"contract-harness refused: {exc}", file=sys.stderr)
        return 2
    return asyncio.run(serve(config))


if __name__ == "__main__":
    raise SystemExit(main())
