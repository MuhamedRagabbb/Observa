"""P5-F T1: the controller's commands to its targets must not depend on reusing an idle connection.

A target (uvicorn) closes an idle keep-alive connection after its timeout. When that happens just as the
controller reuses the pooled connection, the request is lost: httpx raises RemoteProtocolError ("Server
disconnected without sending a response"), the target never saw the command, and the controller reports
503 target_unavailable for a target that was up. The race was reproduced against the real contract stub under
uvicorn (2 of 103 trials inside the race window, each just under the server's keep-alive deadline; 0 of 87 with
no idle reuse; the counts and settings were kept in uncommitted local evidence) and is the LIKELY cause of the 503 seen in
a T1 browser run; that attribution is probable, not proven, because the controller discarded the underlying
exception. The raw server below makes the mechanism deterministic: it answers the first request on a
connection and drops any further request on the SAME connection. Every request opens its own connection
here, so nothing reuses one. Loopback, ephemeral port only.

The production client (trust_env=True) is used on purpose, so inherited proxy settings are neutralised for
every test in this file by the autouse fixture below (test-scoped; no global setting is changed).
"""

import asyncio
import os
import socket
import urllib.request

import httpx
import pytest

from controllers.fault_injector.app import Controller
from services.shared.errors import AppError

OK = b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 2\r\n\r\n{}"
SETTINGS = {"payment_failure_rate": 0.9, "payment_failure_status": 503, "expires_at": 4102444800.0}


class Target:
    """Minimal HTTP/1.1 target: per connection, answer the first request; then act as configured."""

    def __init__(self, *, second="drop", first="answer"):
        self.second, self.first = second, first
        self.connections = 0
        self.requests = []  # (connection number, request line)
        self.server = None

    async def start(self):
        self.server = await asyncio.start_server(self.handle, "127.0.0.1", 0)
        return f"http://127.0.0.1:{self.server.sockets[0].getsockname()[1]}"

    async def handle(self, reader, writer):
        self.connections += 1
        number, served = self.connections, 0
        try:
            while True:
                head = await reader.readuntil(b"\r\n\r\n")
                length = next((int(line.split(b":", 1)[1]) for line in head.split(b"\r\n")
                               if line.lower().startswith(b"content-length:")), 0)
                if length:
                    await reader.readexactly(length)
                self.requests.append((number, head.split(b"\r\n", 1)[0].decode()))
                served += 1
                behaviour = self.first if served == 1 else self.second
                if behaviour == "drop":
                    return  # close without a response, as an idle timer firing at reuse does
                writer.write(OK if behaviour == "answer" else
                             b"HTTP/1.1 500 Internal Server Error\r\nContent-Length: 0\r\n\r\n")
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            return
        finally:
            writer.close()

    async def stop(self):
        self.server.close()
        await self.server.wait_closed()


@pytest.fixture(autouse=True)
def no_inherited_proxy(monkeypatch):
    """Keep the production client (trust_env=True) away from any proxy the machine configures.

    Deleting the proxy variables is not enough on Windows: with none set, urllib falls back to the
    registry's proxy settings. A lone NO_PROXY makes the environment "non-empty" (the registry is then
    skipped) and httpx maps loopback to "no proxy".
    """
    for name in [name for name in os.environ if name.lower().endswith("_proxy")]:
        monkeypatch.delenv(name)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")


def test_inherited_proxy_settings_cannot_reach_these_tests():
    assert set(urllib.request.getproxies()) <= {"no"}
    assert httpx._utils.get_environment_proxies() == {"all://127.0.0.1": None, "all://localhost": None}


@pytest.fixture
async def target():
    servers = []

    async def make(**kwargs):
        server = Target(**kwargs)
        url = await server.start()
        servers.append(server)
        return server, url

    yield make
    for server in servers:
        await server.stop()


def controller(tmp_path, url, monkeypatch):
    monkeypatch.setenv("PAYMENTS_CONTROL_URL", url)
    monkeypatch.setenv("ORDERS_CONTROL_URL", url)
    return Controller(tmp_path / "incidents.jsonl")  # the DEFAULT target client, as in production


async def test_consecutive_commands_each_use_a_fresh_connection(tmp_path, target, monkeypatch):
    server, url = await target(second="drop")
    ctl = controller(tmp_path, url, monkeypatch)
    try:
        await ctl.send("payments_errors", SETTINGS)
        await ctl.send("payments_errors")  # a reused connection would be dropped here
        await ctl.send("payments_errors", SETTINGS)
    finally:
        await ctl.client.aclose()
    assert server.connections == 3
    assert [connection for connection, _ in server.requests] == [1, 2, 3]
    assert [line.split()[0] for _, line in server.requests] == ["PUT", "DELETE", "PUT"]


async def test_an_activation_after_an_idle_pause_reaches_the_target(tmp_path, target, monkeypatch):
    from controllers.fault_injector.app import Activation

    server, url = await target(second="drop")
    ctl = controller(tmp_path, url, monkeypatch)
    try:
        await ctl.send("payments_errors")  # an earlier command leaves (would leave) a pooled connection
        await asyncio.sleep(0.2)
        status = await ctl.activate(Activation(scenario="payments_errors", duration_seconds=30))
    finally:
        await ctl.client.aclose()
    assert status["state"] == "manual" and status["active"]["status"] == "active"
    assert [line.split()[0] for _, line in server.requests] == ["DELETE", "PUT"]


@pytest.mark.parametrize("first", ["drop", "fail"])
async def test_a_target_that_drops_or_fails_a_command_is_still_target_unavailable(tmp_path, target, monkeypatch, first):
    server, url = await target(first=first)
    ctl = controller(tmp_path, url, monkeypatch)
    try:
        with pytest.raises(AppError) as raised:
            await ctl.send("payments_errors", SETTINGS)
    finally:
        await ctl.client.aclose()
    assert (raised.value.status, raised.value.code) == (503, "target_unavailable")
    assert len(server.requests) == 1  # one attempt only: commands are never retried


async def test_a_dropped_activation_stays_unconfirmed_and_unknown(tmp_path, target, monkeypatch):
    from controllers.fault_injector.app import Activation

    server, url = await target(first="drop")
    ctl = controller(tmp_path, url, monkeypatch)
    try:
        with pytest.raises(AppError):
            await ctl.activate(Activation(scenario="payments_errors", duration_seconds=30))
        status = ctl.status()
    finally:
        await ctl.client.aclose()
    assert status["state"] == "unknown" and status["active"]["status"] == "activation_unconfirmed"
    assert len(server.requests) == 1


async def test_an_unreachable_target_is_target_unavailable(tmp_path, monkeypatch):
    # Bound but never listening, and held open for the whole test: no other process can take the port, and
    # Windows refuses (sometimes after ~2 s, inside the client's 3 s budget). A refusal and a connect
    # timeout both mean "not acknowledged", which is what is asserted.
    with socket.socket() as holder:
        holder.bind(("127.0.0.1", 0))
        ctl = controller(tmp_path, f"http://127.0.0.1:{holder.getsockname()[1]}", monkeypatch)
        try:
            with pytest.raises(AppError) as raised:
                await ctl.send("payments_errors", SETTINGS)
        finally:
            await ctl.client.aclose()
    assert (raised.value.status, raised.value.code) == (503, "target_unavailable")


def test_the_default_target_client_keeps_its_three_second_timeout(tmp_path):
    ctl = Controller(tmp_path / "incidents.jsonl")
    assert ctl.client.timeout == httpx.Timeout(3)
