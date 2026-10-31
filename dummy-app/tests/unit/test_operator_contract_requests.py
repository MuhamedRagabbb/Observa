"""P5-F Gate A: header evidence. The harness records which headers the controller really received, so a
browser test can tell a genuine Chromium request (its own Origin and Sec-Fetch-* headers) from one that
the Playwright fixture re-issued from Node (labelled with X-Contract-Reissued; its Sec-Fetch-* headers
are not browser evidence). Nothing here opens a socket or starts the controller."""

import httpx
import pytest

from tests.harness import operator_contract as harness

TEST = {"X-Contract-Harness": "1"}


async def echo_app(scope, receive, send):
    """A stand-in for the controller: answers 200 to every http request."""
    if scope["type"] != "http":
        return
    await receive()
    await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/plain")]})
    await send({"type": "http.response.body", "body": b"ok"})


@pytest.fixture
def state():
    return harness.StubState()


@pytest.fixture
async def stub(state):
    transport = httpx.ASGITransport(app=harness.build_stub_app(state))
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:18191") as client:
        yield client


@pytest.fixture
async def controller(state):
    transport = httpx.ASGITransport(app=harness.RecordingApp(echo_app, state))
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:18190") as client:
        yield client


async def recorded(stub):
    response = await stub.get("/__test__/requests", headers=TEST)
    assert response.status_code == 200
    return response.json()["requests"]


async def test_a_request_is_recorded_with_only_the_headers_that_matter(controller, stub):
    await controller.post(
        "/faults/auto/stop",
        headers={
            "Origin": "http://127.0.0.1:18190",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
            "Cookie": "secret=1",
            "Authorization": "Bearer secret",
        },
    )
    [entry] = await recorded(stub)
    assert entry["method"] == "POST"
    assert entry["path"] == "/faults/auto/stop"
    assert entry["host"] == "127.0.0.1:18190"
    assert entry["origin"] == "http://127.0.0.1:18190"
    assert entry["sec_fetch_site"] == "same-origin"
    assert entry["sec_fetch_mode"] == "cors"
    assert entry["sec_fetch_dest"] == "empty"
    assert entry["reissued"] is None
    assert "at" in entry
    assert "secret" not in str(entry)  # no cookie, no authorization, no other header


async def test_missing_headers_are_null_and_the_query_is_never_kept(controller, stub):
    await controller.get("/faults/status?token=abc&x=1")
    [entry] = await recorded(stub)
    assert entry["path"] == "/faults/status"
    assert "abc" not in str(entry)
    assert (entry["origin"], entry["sec_fetch_site"], entry["sec_fetch_mode"], entry["reissued"]) == (None, None, None, None)


async def test_a_request_the_fixture_re_issued_carries_its_label(controller, stub):
    await controller.get("/faults/status", headers={"X-Contract-Reissued": "route.fetch"})
    [entry] = await recorded(stub)
    assert entry["reissued"] == "route.fetch"


async def test_a_repeated_header_is_recorded_as_such_not_as_the_first_value(controller, stub):
    request = controller.build_request(
        "POST", "/faults/auto/stop", headers=[("origin", "http://a.test"), ("origin", "http://b.test")]
    )
    assert [value for key, value in request.headers.raw if key == b"origin"] == [b"http://a.test", b"http://b.test"]
    await controller.send(request)
    [entry] = await recorded(stub)
    assert entry["origin"] == "<repeated>"


async def test_header_values_are_bounded(controller, stub):
    await controller.get("/faults/status", headers={"Origin": "x" * 5000})
    [entry] = await recorded(stub)
    assert len(entry["origin"]) <= 200


async def test_the_record_is_bounded_oldest_first(controller, stub):
    for number in range(harness.MAX_CALLS + 5):
        await controller.get(f"/n/{number}")
    entries = await recorded(stub)
    assert len(entries) == harness.MAX_CALLS
    assert entries[0]["path"] == "/n/5"
    assert entries[-1]["path"] == f"/n/{harness.MAX_CALLS + 4}"


async def test_reset_clears_the_record(controller, stub):
    await controller.get("/faults/status")
    assert len(await recorded(stub)) == 1
    await stub.post("/__test__/reset", headers=TEST)
    assert await recorded(stub) == []


async def test_reading_the_record_needs_the_harness_header(stub):
    assert (await stub.get("/__test__/requests")).status_code == 403


async def test_the_recorder_passes_everything_through_untouched(controller):
    response = await controller.post("/faults/auto/stop", content=b"body")
    assert (response.status_code, response.text) == (200, "ok")


async def test_non_http_scopes_pass_through_without_a_record(state):
    seen = []

    async def lifespan_app(scope, receive, send):
        seen.append(scope["type"])

    app = harness.RecordingApp(lifespan_app, state)
    await app({"type": "lifespan"}, None, None)
    assert seen == ["lifespan"]
    assert list(state.requests) == []


# --- a redirect that comes from the ALLOWED origin (browser-boundary self-tests) -------------------
# The controller never redirects, so isolation tests need a harness-only source of redirects on the
# controller's own origin. It is served by the recorder, never by the controller, and only to code that
# can send the harness header (a web page cannot do that cross-origin).


@pytest.fixture
async def counted(state):
    calls = []

    async def counting_app(scope, receive, send):
        calls.append(scope.get("path"))
        await echo_app(scope, receive, send)

    transport = httpx.ASGITransport(app=harness.RecordingApp(counting_app, state, control_port=18191))
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:18190") as client:
        yield client, calls


async def test_the_harness_redirect_answers_with_the_requested_status_and_location(counted):
    client, calls = counted
    target = "http://127.0.0.1:18191/sentinel"
    response = await client.get("/__harness__/redirect", params={"to": target}, headers=TEST)
    assert (response.status_code, response.headers["location"]) == (302, target)
    for status in (301, 302, 303, 307, 308):
        again = await client.get("/__harness__/redirect", params={"to": target, "status": status}, headers=TEST)
        assert (again.status_code, again.headers["location"]) == (status, target)
    assert calls == []  # the controller never saw any of it


@pytest.mark.parametrize("status", ["200", "304", "300", "399", "abc", "", "302.0", "-302"])
async def test_only_redirect_statuses_are_served(counted, status):
    client, _ = counted
    response = await client.get(
        "/__harness__/redirect", params={"to": "http://127.0.0.1:18191/sentinel", "status": status}, headers=TEST
    )
    assert response.status_code == 400


@pytest.mark.parametrize(
    "target",
    ["", "javascript:alert(1)", "file:///C:/x", "data:text/html,x", "/relative", "//host/x", "ftp://h/x", "http://", pytest.param("x" * 3000, id="too-long")],
)
async def test_only_absolute_http_targets_are_served(counted, target):
    client, _ = counted
    response = await client.get("/__harness__/redirect", params={"to": target}, headers=TEST)
    assert response.status_code == 400
    assert "location" not in response.headers


async def test_the_harness_redirect_needs_the_header_and_a_loopback_host(counted, state):
    client, _ = counted
    assert (await client.get("/__harness__/redirect", params={"to": "http://127.0.0.1:1/x"})).status_code == 403
    transport = httpx.ASGITransport(app=harness.RecordingApp(echo_app, state))
    async with httpx.AsyncClient(transport=transport, base_url="http://example.test:18190") as foreign:
        response = await foreign.get("/__harness__/redirect", params={"to": "http://127.0.0.1:1/x"}, headers=TEST)
        assert response.status_code == 403
        assert "location" not in response.headers


async def test_the_harness_redirect_is_get_only_and_nothing_else_under_its_prefix_reaches_the_controller(counted):
    client, calls = counted
    assert (await client.post("/__harness__/redirect", params={"to": "http://127.0.0.1:1/x"}, headers=TEST)).status_code == 405
    assert (await client.get("/__harness__/other", headers=TEST)).status_code == 404
    assert (await client.get("/__harness__/", headers=TEST)).status_code == 404
    assert calls == []


async def test_the_harness_redirect_is_recorded_without_its_target(counted, stub):
    client, _ = counted
    await client.get("/__harness__/redirect", params={"to": "http://127.0.0.1:18191/secret-target"}, headers=TEST)
    entries = await recorded(stub)
    assert [entry["path"] for entry in entries] == ["/__harness__/redirect"]
    assert "secret-target" not in str(entries)


# --- a single-use nonce, so a redirect test can be a plain simple GET (no custom header, no preflight) --
# A same-origin fetch with the harness header that is redirected cross-origin would trigger a CORS
# preflight, and the sentinel would see an OPTIONS request instead of the GET: the test could then pass
# although the browser boundary did nothing. The nonce is minted by the Node-side test API.

HEX32 = "[0-9a-f]{32}"


async def mint(stub):
    response = await stub.post("/__test__/redirect-nonce", headers=TEST)
    assert response.status_code == 200, response.text
    return response.json()["nonce"]


async def test_a_nonce_is_minted_through_the_test_api_only(stub):
    import re

    first, second = await mint(stub), await mint(stub)
    assert re.fullmatch(HEX32, first) and re.fullmatch(HEX32, second)
    assert first != second
    assert (await stub.post("/__test__/redirect-nonce")).status_code == 403


async def test_a_nonce_authorizes_one_redirect_without_any_header(counted, stub):
    client, calls = counted
    nonce = await mint(stub)
    params = {"to": "http://127.0.0.1:18191/sentinel", "nonce": nonce}
    first = await client.get("/__harness__/redirect", params=params)  # no X-Contract-Harness header
    assert (first.status_code, first.headers["location"]) == (302, "http://127.0.0.1:18191/sentinel")
    again = await client.get("/__harness__/redirect", params=params)
    assert again.status_code == 403
    assert "location" not in again.headers
    assert calls == []


@pytest.mark.parametrize("nonce", ["", "0" * 32, "deadbeef", "g" * 32, "A" * 32, "x" * 500])
async def test_a_wrong_nonce_is_refused(counted, stub, nonce):
    client, _ = counted
    await mint(stub)
    response = await client.get("/__harness__/redirect", params={"to": "http://127.0.0.1:18191/sentinel", "nonce": nonce})
    assert response.status_code == 403
    assert "location" not in response.headers


async def test_a_nonce_opens_nothing_else(counted, stub):
    client, _ = counted
    nonce = await mint(stub)
    assert (await client.get("/__harness__/other", params={"nonce": nonce})).status_code in (403, 404)
    assert (await stub.get("/__test__/calls", params={"nonce": nonce})).status_code == 403


async def test_a_nonce_does_not_replace_the_validation_of_the_target(counted, stub):
    client, _ = counted
    nonce = await mint(stub)
    response = await client.get("/__harness__/redirect", params={"to": "javascript:alert(1)", "nonce": nonce})
    assert response.status_code == 400
    assert "location" not in response.headers


async def test_the_header_still_authorizes_and_leaves_an_unused_nonce_alone(counted, stub):
    client, _ = counted
    nonce = await mint(stub)
    by_header = await client.get("/__harness__/redirect", params={"to": "http://127.0.0.1:18191/sentinel"}, headers=TEST)
    assert by_header.status_code == 302
    by_nonce = await client.get("/__harness__/redirect", params={"to": "http://127.0.0.1:18191/sentinel", "nonce": nonce})
    assert by_nonce.status_code == 302


async def test_reset_forgets_every_outstanding_nonce(counted, stub):
    client, _ = counted
    nonce = await mint(stub)
    await stub.post("/__test__/reset", headers=TEST)
    response = await client.get("/__harness__/redirect", params={"to": "http://127.0.0.1:18191/sentinel", "nonce": nonce})
    assert response.status_code == 403


async def test_outstanding_nonces_are_bounded_oldest_first(counted, stub):
    client, _ = counted
    nonces = [await mint(stub) for _ in range(harness.MAX_NONCES + 4)]
    assert harness.MAX_NONCES == 16
    stale = await client.get("/__harness__/redirect", params={"to": "http://127.0.0.1:18191/sentinel", "nonce": nonces[0]})
    fresh = await client.get("/__harness__/redirect", params={"to": "http://127.0.0.1:18191/sentinel", "nonce": nonces[-1]})
    assert (stale.status_code, fresh.status_code) == (403, 302)


async def test_the_nonce_is_never_recorded(counted, stub):
    client, _ = counted
    nonce = await mint(stub)
    await client.get("/__harness__/redirect", params={"to": "http://127.0.0.1:18191/sentinel", "nonce": nonce})
    assert nonce not in str(await recorded(stub))


# --- the redirect source can only point at harness-style loopback destinations -----------------------
# Even with a valid header or nonce, the endpoint must never emit a Location that names a live or preview
# port, a non-loopback host, an ambiguous spelling or user info: if the browser boundary ever failed, the
# harness itself must still not be able to send a browser to a real service.

import json as _json  # noqa: E402
from pathlib import Path as _Path  # noqa: E402

_PORTS = _json.loads((_Path(__file__).resolve().parents[2] / "web" / "contracts" / "stack-ports.json").read_text("utf-8"))
_FORBIDDEN_PORTS = [*_PORTS["live"], *_PORTS["preview"].values()]
_LOOPBACK_SPELLINGS = ["127.0.0.1", "localhost", "[::1]"]


@pytest.mark.parametrize("port", _FORBIDDEN_PORTS)
async def test_no_live_or_preview_port_can_be_a_redirect_target(counted, port):
    client, _ = counted
    for host in _LOOPBACK_SPELLINGS:
        response = await client.get("/__harness__/redirect", params={"to": f"http://{host}:{port}/x"}, headers=TEST)
        assert response.status_code == 400, f"{host}:{port}"
        assert "location" not in response.headers


@pytest.mark.parametrize(
    "target",
    [
        "http://example.com:18191/x",
        "http://10.0.0.5:18191/x",
        "http://192.168.1.2:18191/x",
        "http://0.0.0.0:18191/x",
        "http://127.1:18191/x",
        "http://0x7f.0.0.1:18191/x",
        "http://2130706433:18191/x",
        "http://127.0.0.1.:18191/x",
        "http://localhost.:18191/x",
        "http://LOCALHOST:18191/x",
        "http://[::ffff:127.0.0.1]:18191/x",
        "http://[0:0:0:0:0:0:0:1]:18191/x",
        "http://user@127.0.0.1:18191/x",
        "http://127.0.0.1:18191@evil.test/x",
        "http://127.0.0.1:18191\@evil.test/x",
        "http://127.0.0.1%2e:18191/x",
        "https://127.0.0.1:18191/x",
        "http://127.0.0.1/x",
        "http://127.0.0.1:/x",
        "http://127.0.0.1:0/x",
        "http://127.0.0.1:65536/x",
        "http://127.0.0.1:018191/x",
        "http://127.0.0.1:+18191/x",
        "http://127.0.0.1:18191evil/x",
    ],
)
async def test_only_canonical_loopback_http_targets_with_an_explicit_port_are_served(counted, target):
    client, _ = counted
    response = await client.get("/__harness__/redirect", params={"to": target}, headers=TEST)
    assert response.status_code == 400, target
    assert "location" not in response.headers


# --- the redirect source points ONLY at the harness's own sentinel ------------------------------------
# A deny-list of live and preview ports is incomplete by nature (other local services exist, e.g. n8n on
# 5678). The only legitimate use is the sentinel on the harness control origin, so that is all it serves.


@pytest.mark.parametrize(
    "target",
    [
        "http://127.0.0.1:18191/sentinel",
        "http://127.0.0.1:18191/sentinel?x=1#h",
        "http://127.0.0.1:18191/sentinel/deeper",
        "http://127.0.0.1:18191/sentinel/a-b_c/",
    ],
)
async def test_the_sentinel_on_the_harness_control_origin_is_the_only_target(counted, target):
    client, _ = counted
    response = await client.get("/__harness__/redirect", params={"to": target}, headers=TEST)
    assert (response.status_code, response.headers["location"]) == (302, target)


@pytest.mark.parametrize(
    "target",
    [
        "http://127.0.0.1:18191",
        "http://127.0.0.1:18191/",
        "http://127.0.0.1:18191/x",
        "http://127.0.0.1:18191/other",
        "http://127.0.0.1:18191/faults/status",
        "http://127.0.0.1:18191/__test__/reset",
        "http://127.0.0.1:18191/sentinelx",
        "http://127.0.0.1:18191/sentinel/../x",
        "http://127.0.0.1:18191/sentinel/./x",
        "http://127.0.0.1:18191/sentinel/%2e%2e/x",
        "http://127.0.0.1:18191/sentinel%2fx",
        "http://127.0.0.1:18191/sentinel;x",
        "http://127.0.0.1:18191/sentinel/.hidden",
        "http://127.0.0.1:18190/ui/next/",  # the controller's own origin is not a target either
        "http://127.0.0.1:18190/sentinel",
        "http://127.0.0.1:18192/sentinel",  # the next port along
        "http://127.0.0.1:5678/sentinel",  # an unrelated local service
        "http://127.0.0.1:1/x",
        "http://localhost:18191/sentinel",  # only the canonical spelling of the harness origin
        "http://[::1]:18191/sentinel",
    ],
)
async def test_nothing_but_the_sentinel_is_a_target(counted, target):
    client, _ = counted
    response = await client.get("/__harness__/redirect", params={"to": target}, headers=TEST)
    assert response.status_code == 400, target
    assert "location" not in response.headers


async def test_without_a_control_port_the_redirect_source_serves_nothing(state):
    transport = httpx.ASGITransport(app=harness.RecordingApp(echo_app, state))
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:18190") as client:
        response = await client.get(
            "/__harness__/redirect", params={"to": "http://127.0.0.1:18191/sentinel"}, headers=TEST
        )
        assert response.status_code == 400
        assert "location" not in response.headers


@pytest.mark.parametrize("port", [*_FORBIDDEN_PORTS, 0, 80, 1023, 65536, -1, "18191", None.__class__])
def test_the_recorder_refuses_a_control_port_that_could_name_a_real_service(state, port):
    with pytest.raises(harness.UnsafeHarness):
        harness.RecordingApp(echo_app, state, control_port=port)


# --- a harness-only ground-truth probe, for the browser fixture's own self-tests --------------------------
# The real controller never answers with ground truth, so the fixture's "withhold ground truth" path cannot
# be exercised against it. Like the redirect source, the probe lives in the recording wrapper (never in the
# controller), needs the harness header (a nonce opens only the redirect), serves two fixed bodies and nothing
# else, and the controller never sees it.


async def test_the_probe_answers_with_a_fixed_ground_truth_body(counted):
    client, calls = counted
    response = await client.get("/__harness__/ground-truth-probe", headers=TEST)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {"state": "idle", "root_cause_service": "harness-probe"}
    assert calls == []  # the controller never saw it


async def test_the_probe_can_answer_with_json_that_is_not_json(counted):
    client, calls = counted
    response = await client.get("/__harness__/ground-truth-probe", params={"kind": "invalid"}, headers=TEST)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    with pytest.raises(ValueError):
        response.json()
    assert calls == []


@pytest.mark.parametrize("kind", ["", "leak2", "../x", "INVALID", "invalid&kind=leak"])
async def test_the_probe_serves_only_its_two_kinds(counted, kind):
    client, _ = counted
    response = await client.get("/__harness__/ground-truth-probe", params={"kind": kind}, headers=TEST)
    assert response.status_code == 400
    assert "root_cause_service" not in response.text


async def test_the_probe_needs_the_header_and_a_loopback_host(counted, state):
    client, _ = counted
    assert (await client.get("/__harness__/ground-truth-probe")).status_code == 403
    transport = httpx.ASGITransport(app=harness.RecordingApp(echo_app, state))
    async with httpx.AsyncClient(transport=transport, base_url="http://example.test:18190") as foreign:
        response = await foreign.get("/__harness__/ground-truth-probe", headers=TEST)
        assert response.status_code == 403
        assert "root_cause_service" not in response.text


async def test_a_redirect_nonce_does_not_open_the_probe(counted, stub):
    client, calls = counted
    nonce = (await stub.post("/__test__/redirect-nonce", headers=TEST)).json()["nonce"]
    response = await client.get("/__harness__/ground-truth-probe", params={"nonce": nonce})
    assert response.status_code == 403
    assert "root_cause_service" not in response.text
    # ... and the refusal did not consume the nonce
    assert (await client.get("/__harness__/redirect", params={"nonce": nonce, "to": "http://127.0.0.1:18191/sentinel"})).status_code == 302
    assert calls == []


async def test_the_probe_is_get_only(counted):
    client, calls = counted
    assert (await client.post("/__harness__/ground-truth-probe", headers=TEST)).status_code == 405
    assert calls == []


async def test_the_probe_is_recorded_like_any_harness_request(counted, stub):
    client, _ = counted
    await client.get("/__harness__/ground-truth-probe", headers=TEST)
    entries = [row for row in await recorded(stub) if row["path"] == "/__harness__/ground-truth-probe"]
    assert [row["method"] for row in entries] == ["GET"]


# --- probe variants: a clean body, compressed bodies and a held body (all fixed, all harness-only) -------------
# They exist for the fixture's self-tests only: a clean answer to compare the browser's view with, gzip/deflate
# encodings to check the browser sees what Node scanned, and an answer held until the test releases it, so a test
# can end while a Node inspection is provably still running.


async def test_the_probe_can_answer_with_a_clean_body(counted):
    client, calls = counted
    response = await client.get("/__harness__/ground-truth-probe", params={"kind": "clean"}, headers=TEST)
    assert response.status_code == 200
    assert response.json() == {"state": "idle", "probe": "clean"}
    assert calls == []


@pytest.mark.parametrize("encoding", ["gzip", "deflate"])
@pytest.mark.parametrize("kind,expected", [
    ("clean", {"state": "idle", "probe": "clean"}),
    ("leak", {"state": "idle", "root_cause_service": "harness-probe"}),
])
async def test_the_probe_can_compress_its_body(counted, encoding, kind, expected):
    client, _ = counted
    response = await client.get("/__harness__/ground-truth-probe", params={"kind": kind, "encoding": encoding}, headers=TEST)
    assert response.status_code == 200
    assert response.headers["content-encoding"] == encoding
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == expected  # httpx decoded it: the bytes on the wire really were compressed


async def test_an_uncompressed_probe_has_no_content_encoding(counted):
    client, _ = counted
    response = await client.get("/__harness__/ground-truth-probe", headers=TEST)
    assert "content-encoding" not in response.headers


async def test_the_held_probe_waits_for_a_release_and_not_for_the_clock(counted, state):
    import asyncio

    client, calls = counted
    state.probe_hold_limit = 5  # far above this test: only the release may end the wait
    answer = asyncio.create_task(client.get("/__harness__/ground-truth-probe", params={"hold": "1"}, headers=TEST))
    await asyncio.sleep(0.3)
    assert not answer.done(), "a held probe answered before it was released"
    state.release()
    response = await asyncio.wait_for(answer, 2)
    assert response.json() == {"state": "idle", "root_cause_service": "harness-probe"}
    assert calls == []


async def test_a_held_probe_is_still_bounded(counted, state):
    import asyncio

    client, _ = counted
    state.probe_hold_limit = 0.2  # nothing releases it: the probe's own hold limit ends the wait
    response = await asyncio.wait_for(client.get("/__harness__/ground-truth-probe", params={"hold": "1"}, headers=TEST), 3)
    assert response.status_code == 200


@pytest.mark.parametrize("hold,expected_waits", [("0", 0), ("1", 1)])
async def test_only_hold_one_waits(counted, state, hold, expected_waits):
    client, _ = counted
    waits = []

    async def recording_hold():
        waits.append(1)

    state.probe_hold = recording_hold
    response = await client.get("/__harness__/ground-truth-probe", params={"hold": hold}, headers=TEST)
    assert response.status_code == 200
    assert len(waits) == expected_waits  # no wall-clock assertion: whether the wait was entered at all


@pytest.mark.parametrize("query", [
    "encoding=br", "encoding=GZIP", "encoding=", "encoding=gzip&encoding=gzip", "kind=leak&kind=clean",
    "hold=2", "hold=", "hold=true", "hold=1&hold=1", "slow=1", "unknown=1", "kind=leak&extra=x", "nonce=" + "0" * 32,
])
async def test_the_probe_refuses_anything_but_its_fixed_options(counted, query):
    client, calls = counted
    response = await client.get(f"/__harness__/ground-truth-probe?{query}", headers=TEST)
    assert response.status_code == 400
    assert "root_cause_service" not in response.text
    assert calls == []
