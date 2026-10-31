"""P5-F Gate A review fixes: the sentinel ledger, the anchored journal and the redirect parameters.

The sentinel proves "the forbidden destination received nothing". Counting only GETs would miss an OPTIONS,
HEAD, POST or WebSocket handshake, so every contact is recorded at the control app's outermost ASGI
boundary, before method routing. Nothing here opens a socket or starts the controller."""

import asyncio

import httpx
import pytest

from tests.harness import operator_contract as harness

TEST = {"X-Contract-Harness": "1"}
METHODS = ["GET", "HEAD", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"]


@pytest.fixture
def state():
    return harness.StubState()


@pytest.fixture
async def stub(state):
    transport = httpx.ASGITransport(app=harness.build_stub_app(state))
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:18191") as client:
        yield client


async def hits(stub):
    return (await stub.get("/__test__/sentinel", headers=TEST)).json()["hits"]


async def contacts(stub):
    response = await stub.get("/__test__/sentinel-contacts", headers=TEST)
    assert response.status_code == 200
    return response.json()["contacts"]


# --- every contact counts, whatever the method ------------------------------------------------------


async def test_every_method_that_reaches_the_sentinel_is_counted(stub):
    for method in METHODS:
        await stub.request(method, "/sentinel")
    assert await hits(stub) == len(METHODS)
    assert [entry["method"] for entry in await contacts(stub)] == METHODS


async def test_a_get_is_counted_exactly_once(stub):
    assert (await stub.get("/sentinel")).status_code == 200
    assert await hits(stub) == 1


async def test_a_path_under_the_sentinel_counts_and_a_lookalike_does_not(stub):
    await stub.get("/sentinel/deeper/path")
    await stub.get("/sentinelx")
    await stub.get("/sentinel-other")
    await stub.put("/orders/_control/runtime", json={})
    assert await hits(stub) == 1
    assert [entry["path"] for entry in await contacts(stub)] == ["/sentinel/deeper/path"]


async def test_a_contact_counts_even_when_the_host_or_header_would_be_refused(state):
    transport = httpx.ASGITransport(app=harness.build_stub_app(state))
    async with httpx.AsyncClient(transport=transport, base_url="http://example.test:18191") as foreign:
        await foreign.get("/sentinel")  # refused by the Host guard, but it reached the server
        await foreign.post("/sentinel")
    assert len(state.sentinel_contacts) == 2


async def test_a_websocket_handshake_to_the_sentinel_is_counted_and_refused(state):
    app = harness.build_stub_app(state)
    sent = []

    async def receive():
        return {"type": "websocket.connect"}

    async def send(message):
        sent.append(message["type"])

    scope = {
        "type": "websocket", "path": "/sentinel", "raw_path": b"/sentinel", "query_string": b"", "scheme": "ws",
        "headers": [(b"host", b"127.0.0.1:18191")], "subprotocols": [], "server": ("127.0.0.1", 18191),
        "client": ("127.0.0.1", 50000), "root_path": "", "asgi": {"version": "3.0"},
    }
    await asyncio.wait_for(app(scope, receive, send), 5)
    assert [entry["method"] for entry in state.sentinel_contacts] == ["WEBSOCKET"]
    assert "websocket.accept" not in sent  # the harness never accepts a connection


async def test_the_ledger_is_bounded_oldest_first(stub, state):
    for number in range(harness.MAX_CALLS + 5):
        await stub.get(f"/sentinel/{number}")
    entries = await contacts(stub)
    assert len(entries) == harness.MAX_CALLS
    assert entries[0]["path"] == "/sentinel/5"
    assert await hits(stub) == harness.MAX_CALLS + 5  # the count is not capped, only the listing


async def test_reset_clears_the_ledger_and_the_count(stub):
    await stub.get("/sentinel")
    await stub.post("/__test__/reset", headers=TEST)
    assert await hits(stub) == 0
    assert await contacts(stub) == []


async def test_reading_the_ledger_needs_the_harness_header(stub):
    assert (await stub.get("/__test__/sentinel-contacts")).status_code == 403


async def test_the_ledger_never_keeps_a_query_or_a_header(stub):
    await stub.get("/sentinel?secret=1", headers={"Authorization": "Bearer secret", "Cookie": "s=1"})
    [entry] = await contacts(stub)
    assert set(entry) == {"method", "path", "at"}
    assert "secret" not in str(entry)


# --- the redirect parameters stay unambiguous (regression tests for the review's mutation list) ----------


@pytest.fixture
async def redirector(state):
    async def echo(scope, receive, send):
        await receive()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    transport = httpx.ASGITransport(app=harness.RecordingApp(echo, state, control_port=18191))
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:18190") as client:
        yield client


SENTINEL = "http://127.0.0.1:18191/sentinel"


async def test_a_repeated_target_or_status_is_refused(redirector):
    for query in (f"to={SENTINEL}&to={SENTINEL}", f"to={SENTINEL}&status=302&status=307"):
        response = await redirector.get(f"/__harness__/redirect?{query}", headers=TEST)
        assert response.status_code == 400, query
        assert "location" not in response.headers


async def test_a_repeated_nonce_is_refused_and_not_consumed(redirector, state):
    nonce = state.mint_redirect_nonce()
    response = await redirector.get(f"/__harness__/redirect?to={SENTINEL}&nonce={nonce}&nonce={nonce}")
    assert response.status_code == 403
    assert "location" not in response.headers
    assert (await redirector.get(f"/__harness__/redirect?to={SENTINEL}&nonce={nonce}")).status_code == 302


async def test_a_repeated_harness_header_is_refused_and_never_consumes_a_nonce(redirector, state):
    nonce = state.mint_redirect_nonce()
    request = redirector.build_request(
        "GET", f"/__harness__/redirect?to={SENTINEL}&nonce={nonce}", headers=[("x-contract-harness", "1"), ("x-contract-harness", "1")]
    )
    assert (await redirector.send(request)).status_code == 403
    assert (await redirector.get(f"/__harness__/redirect?to={SENTINEL}&nonce={nonce}")).status_code == 302


# --- the journal folder is anchored to the repository, not to wherever the output root points -------------


def test_the_journal_anchor_is_the_resolved_repository_output_folder():
    assert harness.JOURNAL_ANCHOR == harness.ROOT.resolve() / "web" / "e2e-output"


def test_a_relocated_output_root_is_refused(tmp_path, monkeypatch):
    # e.g. web/e2e-output is a junction or symlink to an unrelated folder: root and journal folder move together,
    # so containment alone would pass. Both resolved paths must still sit under the repository's own folder.
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "contract-journal").mkdir(parents=True)
    (elsewhere / "contract-journal" / "incidents.jsonl").write_text("unrelated", "utf-8")
    monkeypatch.setattr(harness, "JOURNAL_ROOT", elsewhere)
    monkeypatch.setattr(harness, "JOURNAL_DIR", elsewhere / "contract-journal")
    monkeypatch.setattr(harness, "JOURNAL_ANCHOR", tmp_path / "the-repository" / "web" / "e2e-output")
    with pytest.raises(harness.UnsafeHarness):
        harness.prepare_journal()
    assert (elsewhere / "contract-journal" / "incidents.jsonl").read_text("utf-8") == "unrelated"


def test_the_anchored_default_works_when_root_and_anchor_agree(tmp_path, monkeypatch):
    root = tmp_path / "web" / "e2e-output"
    (root / "contract-journal").mkdir(parents=True)
    (root / "contract-journal" / "incidents.jsonl").write_text("old", "utf-8")
    monkeypatch.setattr(harness, "JOURNAL_ROOT", root)
    monkeypatch.setattr(harness, "JOURNAL_DIR", root / "contract-journal")
    monkeypatch.setattr(harness, "JOURNAL_ANCHOR", root.resolve())
    path = harness.prepare_journal()
    assert path == (root / "contract-journal").resolve() / "incidents.jsonl"
    assert not path.exists()


# --- review round 2: absolute-form targets, a bodyless redirect, an internal journal link --------------


@pytest.mark.parametrize(
    ("path", "counted"),
    [
        ("http://127.0.0.1:18191/sentinel", True),  # h11 keeps an absolute-form target in scope["path"]
        ("http://127.0.0.1:18191/sentinel/deeper", True),
        ("http://example.test/sentinel", True),  # whatever the authority, it reached THIS server's sentinel
        ("http://127.0.0.1:18191/%73entinel", True),  # an encoded letter decodes once
        ("http://127.0.0.1:18191/sentinelx", False),
        ("http://127.0.0.1:18191", False),
        ("http://127.0.0.1:18191/other/sentinel", False),
        ("HTTP://127.0.0.1:18191/sentinel", True),  # the scheme's case does not matter
        ("/sentinel", True),
        ("/%73entinel", True),
        ("/Sentinel", False),
        ("//sentinel", False),
    ],
)
async def test_the_ledger_reads_absolute_form_and_encoded_targets_the_way_the_server_does(state, path, counted):
    app = harness.build_stub_app(state)
    scope = {
        "type": "http", "method": "GET", "path": path, "raw_path": path.encode(), "query_string": b"", "scheme": "http",
        "headers": [(b"host", b"127.0.0.1:18191")], "server": ("127.0.0.1", 18191), "client": ("127.0.0.1", 50000),
        "root_path": "", "http_version": "1.1", "asgi": {"version": "3.0"},
    }

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        pass

    await asyncio.wait_for(app(scope, receive, send), 5)
    assert state.sentinel_hits == (1 if counted else 0), path


async def test_the_redirect_answer_has_no_body_and_no_json_type():
    # Playwright cannot read the body of a 3xx response: a JSON body would make the leak scan fail for a
    # reason that has nothing to do with prevention.
    state = harness.StubState()

    async def echo(scope, receive, send):
        await receive()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    transport = httpx.ASGITransport(app=harness.RecordingApp(echo, state, control_port=18191))
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:18190") as client:
        response = await client.get(
            "/__harness__/redirect", params={"to": "http://127.0.0.1:18191/sentinel"}, headers=TEST
        )
    assert response.status_code == 302
    assert response.content == b""
    assert "json" not in response.headers.get("content-type", "")
    assert response.headers["location"] == "http://127.0.0.1:18191/sentinel"


def _link(link, target):
    import os
    import sys

    target.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        os.symlink(target, link, target_is_directory=True)


def test_a_journal_folder_that_links_elsewhere_inside_the_output_root_is_refused(tmp_path, monkeypatch):
    root = tmp_path / "web" / "e2e-output"
    other = root / "other-artifacts"
    other.mkdir(parents=True)
    (other / "incidents.jsonl").write_text("not the harness journal", "utf-8")
    try:
        _link(root / "contract-journal", other)
    except (OSError, AttributeError):
        pytest.skip("cannot create links here")
    monkeypatch.setattr(harness, "JOURNAL_ROOT", root)
    monkeypatch.setattr(harness, "JOURNAL_DIR", root / "contract-journal")
    monkeypatch.setattr(harness, "JOURNAL_ANCHOR", root.resolve())
    with pytest.raises(harness.UnsafeHarness):
        harness.prepare_journal()
    assert (other / "incidents.jsonl").read_text("utf-8") == "not the harness journal"


def test_a_journal_folder_outside_the_anchored_root_is_refused_by_default(tmp_path, monkeypatch):
    root = tmp_path / "web" / "e2e-output"
    root.mkdir(parents=True)
    monkeypatch.setattr(harness, "JOURNAL_ROOT", root)
    monkeypatch.setattr(harness, "JOURNAL_ANCHOR", root.resolve())
    monkeypatch.setattr(harness, "JOURNAL_DIR", tmp_path / "elsewhere")
    with pytest.raises(harness.UnsafeHarness):
        harness.prepare_journal()
