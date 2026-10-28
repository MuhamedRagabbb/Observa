"""P5-0: the controller refuses state-changing requests that a web page on another origin could
send (cross-site forms or fetches, DNS rebinding). Read-only requests and the CLI (no Origin) are
unaffected. The decision uses the raw Host and Origin headers only: forwarded headers are never
trusted and CORS is not relied on (there is no CORS middleware at all)."""

import json

import httpx
import pytest

from controllers.fault_injector.app import create_app
from controllers.fault_injector.guard import rejection

UNSAFE = ("POST", "PUT", "PATCH", "DELETE")


def headers(**values):
    return [(name.replace("_", "-").lower().encode(), value.encode()) for name, value in values.items()]


@pytest.mark.parametrize(
    "request_headers",
    [
        {"host": "127.0.0.1:8090"},  # the CLI, scripts/faults.py, tests: no Origin
        {"host": "localhost:18090"},
        {"host": "localhost"},
        {"host": "[::1]:8090"},
        {"host": "LOCALHOST:8090"},
        {"host": "127.0.0.1:18090", "origin": "http://127.0.0.1:18090"},  # the operator page itself
        {"host": "localhost:8090", "origin": "http://localhost:8090", "sec_fetch_site": "same-origin"},
        {"host": "127.0.0.1:8090", "sec_fetch_site": "none"},  # typed into the address bar
        {"host": "[::1]:8090", "origin": "http://[::1]:8090"},
    ],
)
@pytest.mark.parametrize("method", UNSAFE)
def test_same_origin_and_cli_requests_are_allowed(method, request_headers):
    assert rejection(method, headers(**request_headers)) is None


@pytest.mark.parametrize(
    ("request_headers", "reason"),
    [
        ({"host": "127.0.0.1:8090", "origin": "null"}, "origin_not_allowed"),  # sandboxed frame, file://
        ({"host": "127.0.0.1:8090", "origin": "https://evil.example"}, "origin_not_allowed"),
        (
            {"host": "127.0.0.1:18090", "origin": "http://127.0.0.1:5181"},
            "origin_not_allowed",
        ),  # another port
        (
            {"host": "127.0.0.1:8090", "origin": "https://127.0.0.1:8090"},
            "origin_not_allowed",
        ),  # another scheme
        (
            {"host": "127.0.0.1:8090", "origin": "http://localhost:8090"},
            "origin_not_allowed",
        ),  # another host name
        (
            {"host": "127.0.0.1:8090", "origin": "http://127.0.0.1:8090/"},
            "origin_not_allowed",
        ),  # not an origin
        ({"host": "127.0.0.1:8090", "origin": "http://user@127.0.0.1:8090"}, "origin_not_allowed"),
        ({"host": "127.0.0.1:8090", "origin": ""}, "origin_not_allowed"),
        ({"host": "evil.example:8090"}, "host_not_allowed"),  # DNS rebinding: the name is the attacker's
        ({"host": "evil.example:8090", "origin": "http://evil.example:8090"}, "host_not_allowed"),
        ({"host": "controller"}, "host_not_allowed"),
        ({"host": "127.0.0.2:8090"}, "host_not_allowed"),
        ({"host": "localhost.:8090"}, "host_not_allowed"),
        ({"host": "user@127.0.0.1:8090"}, "host_not_allowed"),
        ({"host": "127.0.0.1:80900"}, "host_not_allowed"),
        ({"host": "127.0.0.1 :8090"}, "host_not_allowed"),
        ({"host": ""}, "host_not_allowed"),
        ({}, "host_not_allowed"),
        ({"host": "127.0.0.1:8090", "sec_fetch_site": "cross-site"}, "cross_site"),
        ({"host": "127.0.0.1:8090", "sec_fetch_site": "same-site"}, "cross_site"),
        (
            {"host": "127.0.0.1:8090", "origin": "http://127.0.0.1:8090", "sec_fetch_site": "cross-site"},
            "cross_site",
        ),
    ],
)
@pytest.mark.parametrize("method", UNSAFE)
def test_foreign_or_malformed_requests_are_refused(method, request_headers, reason):
    assert rejection(method, headers(**request_headers)) == reason


@pytest.mark.parametrize(
    "request_headers",
    [
        # A proxy header naming loopback never makes a foreign Host acceptable...
        [
            (b"host", b"evil.example"),
            (b"x-forwarded-host", b"127.0.0.1:8090"),
            (b"forwarded", b"host=127.0.0.1:8090"),
        ],
        # ...and never makes a foreign Origin match.
        [
            (b"host", b"127.0.0.1:8090"),
            (b"origin", b"https://evil.example"),
            (b"x-forwarded-host", b"evil.example"),
        ],
        # Two Host or Origin headers are ambiguous: refused, whatever the first one says.
        [(b"host", b"127.0.0.1:8090"), (b"host", b"evil.example")],
        [
            (b"host", b"127.0.0.1:8090"),
            (b"origin", b"http://127.0.0.1:8090"),
            (b"origin", b"https://evil.example"),
        ],
    ],
)
def test_forwarded_and_duplicate_headers_never_help(request_headers):
    assert rejection("POST", request_headers) is not None


@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS"])
def test_reads_need_a_loopback_host_but_no_origin(method):
    # A rebound hostile name must not read the state either (it names the active scenario)...
    assert rejection(method, headers(host="evil.example:8090")) == "host_not_allowed"
    assert (
        rejection(method, headers(host="evil.example", origin="https://evil.example")) == "host_not_allowed"
    )
    # ...but a read changes nothing, so its Origin is not checked (a cross-origin page cannot read
    # the answer anyway: the controller sends no CORS headers).
    assert rejection(method, headers(host="127.0.0.1:8090", origin="https://evil.example")) is None
    assert rejection(method, headers(host="localhost:8090", sec_fetch_site="cross-site")) is None


@pytest.mark.parametrize("method", ["get", "Get", "post", "PROPFIND", "TRACE"])
def test_only_the_exact_read_methods_skip_the_origin_check(method):
    assert (
        rejection(method, headers(host="127.0.0.1:8090", origin="https://evil.example"))
        == "origin_not_allowed"
    )


@pytest.fixture
async def app_client(tmp_path):
    target_calls = []

    def target(request):
        target_calls.append(request)
        return httpx.Response(200, json={"status": "ok"})

    app = create_app(
        path=tmp_path / "incidents.jsonl",
        client=httpx.AsyncClient(transport=httpx.MockTransport(target)),
        health=None,
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://127.0.0.1:8090") as client,
    ):
        yield app, client, target_calls, tmp_path / "incidents.jsonl"


ROUTES = [
    ("/faults/activate", {"scenario": "payments_errors", "duration_seconds": 5}),
    ("/faults/deactivate", {"scenario": "payments_errors"}),
    ("/faults/auto/start", {"scenarios": ["payments_errors"], "warmup_seconds": 60}),
    ("/faults/auto/stop", None),
]


@pytest.mark.parametrize(("path", "body"), ROUTES)
@pytest.mark.parametrize(
    "foreign",
    [
        {"origin": "https://evil.example"},
        {"origin": "null"},
        {"host": "evil.example:8090"},
        {"sec-fetch-site": "cross-site"},
    ],
)
async def test_every_control_endpoint_refuses_a_foreign_request_before_doing_anything(
    app_client, path, body, foreign
):
    app, client, target_calls, journal = app_client
    before = (await client.get("/faults/status")).json()
    calls_before = len(target_calls)  # opening the controller already cleared both targets
    response = await client.post(path, json=body, headers=foreign)
    assert response.status_code == 403
    payload = response.json()
    assert payload["error_code"] in {"origin_not_allowed", "host_not_allowed", "cross_site"}
    assert payload["message"] == "Control requests are accepted only from the controller's own page"
    assert payload["request_id"].startswith("ctl-")
    # Nothing happened: no target was called, no incident recorded, the state is unchanged.
    assert len(target_calls) == calls_before
    after = (await client.get("/faults/status")).json()
    assert {k: v for k, v in after.items() if k != "now"} == {k: v for k, v in before.items() if k != "now"}
    assert not journal.exists() or "payments_errors" not in journal.read_text(encoding="utf-8")


async def test_the_guard_runs_before_validation_so_a_foreign_page_learns_nothing(app_client):
    _, client, _, _ = app_client
    response = await client.post(
        "/faults/activate", content=b"not json", headers={"origin": "https://evil.example"}
    )
    assert response.status_code == 403


async def test_a_refusal_is_logged_with_the_reason_but_never_the_header_values(app_client, capsys):
    _, client, _, _ = app_client
    await client.post("/faults/auto/stop", headers={"origin": "https://evil.example/\nforged"})
    logged = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")]
    refusals = [entry for entry in logged if entry.get("event") == "control_request_refused"]
    assert (
        refusals
        and refusals[-1]["reason"] == "origin_not_allowed"
        and refusals[-1]["path"] == "/faults/auto/stop"
    )
    assert "evil" not in json.dumps(refusals)


async def test_the_operator_page_and_the_cli_still_control_faults(app_client):
    _, client, target_calls, _ = app_client
    calls_before = len(target_calls)
    # The operator page: same origin, as a browser sends it.
    page = {"origin": "http://127.0.0.1:8090", "sec-fetch-site": "same-origin"}
    started = await client.post("/faults/activate", json={"scenario": "payments_errors"}, headers=page)
    assert started.status_code == 200 and started.json()["state"] == "manual"
    # The CLI: no Origin at all.
    stopped = await client.post("/faults/deactivate", json={"scenario": "payments_errors"})
    assert stopped.status_code == 200 and stopped.json()["active"] is None
    assert len(target_calls) > calls_before


async def test_reads_answer_on_loopback_and_refuse_a_rebound_name(app_client):
    _, client, _, _ = app_client
    for path in ("/health", "/faults/status", "/services/health", "/activity", "/", "/ui/config"):
        assert (await client.get(path, headers={"origin": "https://evil.example"})).status_code == 200, path
        refused = await client.get(path, headers={"host": "evil.example:8090"})
        assert refused.status_code == 403 and refused.json()["error_code"] == "host_not_allowed", path


def test_openapi_documents_the_refusal_on_control_endpoints(tmp_path):
    spec = create_app(path=tmp_path / "incidents.jsonl").openapi()
    for path, _ in ROUTES:
        assert "403" in spec["paths"][path]["post"]["responses"], path
    # Reads are refused only for a non-loopback Host, which no legitimate client sends: not documented.
    assert "403" not in spec["paths"]["/faults/status"]["get"]["responses"]
