# OBSERVA dummy commerce environment

A runnable local test bed generating correlated JSON logs, OpenTelemetry metrics and distributed traces. Four Python 3.12/FastAPI services use PostgreSQL 16, with separate databases and credentials. Includes reproducible traffic, three independent reversible failures, an Arabic storefront and a separate evaluation-only operator interface. No OBSERVA agents are included.

See [VALIDATION.md](VALIDATION.md) for the recorded commands, observed live telemetry, acceptance results and practical limits from the implementation run.

> **Location:** this app lives in `dummy-app/` inside the OBSERVA repository. The project overview is in the [root README](../README.md). Run every command below from `dummy-app/`. The approved English storefront, multi-item cart and operator redesign are specified in [IMPLEMENTATION_SPEC.md §12](IMPLEMENTATION_SPEC.md#12-approved-design-reconciliation) and aren't implemented yet.
>
> **Live tests and host ports:** the tests read ports such as `POSTGRES_PORT` from the shell environment, not from `.env`. If you changed a port in `.env`, for example because another PostgreSQL already uses 5432, export the same value before running `pytest`.

## Start

Install Docker Desktop with its Linux engine running (or Docker Engine + Compose v2+). From this directory:

```sh
cp .env.example .env
docker compose up --build -d
docker compose ps
python scripts/smoke.py
```

On PowerShell, use `Copy-Item .env.example .env`. Python 3.12 is only required on the host for scripts/tests; all services run in containers. Images and Python dependencies (including transitive dependencies) are pinned. Allow several minutes for the first download/build. Allocate roughly 2–3 GB to Docker. Container memory/concurrency limits keep this small enough for a laptop.

On Linux, set `LOCAL_UID`/`LOCAL_GID` in `.env` to your `id -u`/`id -g` so the controller can write the bind-mounted `ground-truth` directory. Windows Docker Desktop works with the defaults.

Traffic begins automatically at 4 requests/second with seed 42. All exposed ports bind to `127.0.0.1`. These mock credentials and unauthenticated local business/controller APIs are for local testing.

| Entry point | Default address |
| --- | --- |
| Arabic storefront | http://localhost:8000/ |
| Operator interface (evaluation only) | http://localhost:8090/ |
| Gateway Swagger/OpenAPI | http://localhost:8000/docs / http://localhost:8000/openapi.json |
| Orders / Inventory / Payments Swagger | http://localhost:8001/docs / http://localhost:8002/docs / http://localhost:8003/docs |
| Fault controller Swagger (evaluation only) | http://localhost:8090/docs |
| Prometheus | http://localhost:9090 |
| Jaeger | http://localhost:16686 |
| Loki readiness / API | http://localhost:3100/ready |
| Collector Prometheus scrape endpoint | http://localhost:8889/metrics |
| Collector OTLP HTTP / gRPC | localhost:4318 / localhost:4317 |

Optional Grafana, preconfigured with a dashboard, Loki-to-Jaeger links and all three data sources:

```sh
docker compose --profile ui up -d
```

Open http://localhost:3000/d/observa-overview (anonymous viewing enabled). `telemetry-health` checks the real readiness endpoints for Collector, Prometheus, Jaeger and Loki; application health checks include database readiness. Applications start and continue working without healthy telemetry backends.

## Business API

The built-in Arabic storefront at `http://localhost:8000/` lists live products, filters/searches the catalog, creates real simulated checkouts, and shows success or failure with request/trace IDs and a Jaeger link. The checkout button disables while a request is pending; the browser never automatically retries a checkout. Ambiguous network/timeout results are marked unconfirmed rather than reported as a definite failure or success.

The **My orders** view stores up to 100 recent orders/attempts in that browser's local storage. It is not a global list of traffic-generator orders. Reloading preserves this local history; users can update statuses or import an existing order by its full ID. All transaction state remains owned by the backend databases. Prices are displayed in illustrative USD units from the integer-cent API values; no real charge is made.

The operator interface at `http://localhost:8090/` calls the controller on its own origin. It includes all three manual scenarios with duration/parameter inputs, live state and expiry countdown, stop controls, and automatic warmup/duration/recovery/repeat/seed/scenario settings. Its session activity list contains actions from that page only. It is evaluation infrastructure and must not be scraped or ingested by the investigating agent.

HTML, CSS, JavaScript and SVG assets ship inside the existing image under `services/ui/`; no Node build, CDN, added service, or extra container is required. Storefront and operator pages use separate origins, and the storefront does not proxy fault-control calls through Gateway. Static UI requests and `/ui/config` are excluded from application telemetry, while shopping API requests retain normal instrumentation. Public navigation links follow the Compose port settings through a non-sensitive UI config endpoint. All mock-credential and loopback-only boundaries still apply.

```sh
curl http://localhost:8000/health
curl http://localhost:8000/api/products
curl -i -X POST http://localhost:8000/api/orders -H 'Content-Type: application/json' -H 'X-Request-ID: req-demo' -d '{"product_id":"sku-001","quantity":2,"customer_id":"load-user-17"}'
curl http://localhost:8000/api/orders/ORDER_ID
```

For PowerShell use `curl.exe` or `Invoke-RestMethod`; the cross-platform Python scripts avoid shell quoting issues. For example:

```powershell
$body = @{product_id='sku-001'; quantity=2; customer_id='load-user-17'} | ConvertTo-Json
Invoke-RestMethod http://localhost:8000/api/orders -Method Post -ContentType 'application/json' -Body $body
```

Successful creation returns **201** with `{order_id, product_id, quantity, status, reservation_id, payment_id, error_code}`; status is `completed`. Lookups return **200** with the same schema. Intermediate stored states are `pending` and `inventory_reserved`; unsuccessful workflows are `failed`. If payment commits but a subsequent database write fails, best-effort persistence records `reconciliation_required` and the known payment ID, and emits `order_reconciliation_required`; the request fails rather than reporting completion. Inspect that record before retrying a checkout, since order creation itself is not idempotent. Product listing returns an array of `{product_id, name, price_cents, stock}`.

Every business response has `X-Request-ID` and `X-Trace-ID`. A valid caller request ID (1–100 ASCII letters/digits/hyphens/underscores) is preserved across services; invalid IDs are replaced. Inbound W3C `traceparent` is extracted and propagated. Health endpoints are excluded from ordinary request telemetry; readiness failures remain logged.

Errors consistently return `{error_code, message, request_id, trace_id}`. **422** means invalid input (quantity must be an integer 1–10; customer IDs are 1–64 non-sensitive ASCII letters/digits/hyphens/underscores; product IDs match `sku-NNN`). **404** means unknown product/order/route, **409** means insufficient stock or an idempotency conflict, **402** means synthetic payment decline, **503** means unavailable database capacity/payment processing, **502/504** mean dependency connection failure/timeout, and **500** means a persistence regression or unexpected internal error. Error messages omit payloads, SQL and credentials. Controller errors have `error_code`, `message`, `request_id`; controller responses are evaluation-only.

Direct service APIs:

| API | Request | Result |
| --- | --- | --- |
| `POST :8001/orders` | Same order input | 201 order result |
| `GET :8001/orders/{order_id}` | — | 200 order result / 404 |
| `GET :8002/inventory/products` | — | 200 product array |
| `POST :8002/inventory/reservations` | `{order_id,product_id,quantity}` | 201 `{reservation_id,status}` |
| `POST :8002/inventory/reservations/{id}/release` | — | 200 `{reservation_id,status:"released"}` |
| `POST :8003/payments/charge` | `{order_id,idempotency_key,customer_id,amount_cents}` | 200 `{payment_id,status:"succeeded"}` / 402 decline |

Reservation ID is the order ID, with format `order-UUID`. Repeated identical reservation calls do not consume more stock; mismatched input returns 409. Releases are idempotent (including absent IDs). Payment keys are stable, non-empty strings up to 100 characters; amounts are integer cents in 1–10,000,000. The same key cannot be reused for a different order/customer/amount. Successful and declined decisions are persisted and remain stable across retries. A transient injected 503 is not persisted, allowing recovery with the same key. No payment details or real processor exist.

Inventory uses row locks and transactions; payments use a unique key and `ON CONFLICT` for concurrency safety. Orders compensates with a best-effort inventory release after a failed payment or pre-payment persistence operation. Network retries are bounded and apply only to GETs and idempotent reservation/payment/release operations. Gateway never automatically retries order creation. A simultaneous infrastructure loss can require manual reconciliation: this local simulation does not claim distributed transaction guarantees.

Normal traffic mixes orders (~65%), products (~20%), lookups (~13%), and missing routes (~2%); some order attempts deliberately use invalid quantity, an unknown product, empty `sku-004`, or `decline-*` customers. Other synthetic payments have a deterministic 2% decline policy. `sku-001`–`003` have large finite seed stock so traffic can run for days; `sku-005` is available for isolated tests. `traffic_summary` emits cumulative volumes and rolling latency percentiles every ten seconds in the traffic container's stdout, outside application telemetry. Timing/concurrency are naturally nondeterministic; inputs are repeatable from the seed.

## Manual failures

Reservation operations also use bounded local locks and PostgreSQL transaction advisory locks, so simultaneous retries of the same key cannot consume the entire pool. Releases record tombstones to reject late reservations after timeout compensation. Payments coordinates repeated keys before acquiring database connections and retains its database uniqueness constraint for correctness across processes.

Use either the controller API or standard-library Python CLI. Each activation defaults to a 120-second TTL; maximum 1800 seconds. Each target enforces its own lease, so killing the controller cannot leave a fault enabled indefinitely. Default policy permits exactly one incident at a time. An overlapping activation returns 409. Deactivation is idempotent; deactivating a different name leaves the active incident unchanged. Stop auto mode before manual commands.

```sh
python scripts/faults.py status
python scripts/faults.py activate db_connection_exhaustion --duration 120 --parameters '{"hold_seconds":8}'
python scripts/faults.py deactivate db_connection_exhaustion
python scripts/faults.py activate payments_errors --duration 120 --parameters '{"failure_rate":1,"status_code":503}'
python scripts/faults.py deactivate payments_errors
python scripts/faults.py activate bad_deployment --duration 120 --parameters '{"delay_seconds":1.5}'
python scripts/faults.py deactivate bad_deployment
```

CLI defaults also work without JSON quoting, e.g. `python scripts/faults.py activate payments_errors`.

Equivalent copy/paste curl examples (POSIX shell):

```sh
curl -X POST http://localhost:8090/faults/activate -H 'Content-Type: application/json' -d '{"scenario":"db_connection_exhaustion","duration_seconds":120,"parameters":{"hold_seconds":8}}'
curl -X POST http://localhost:8090/faults/deactivate -H 'Content-Type: application/json' -d '{"scenario":"db_connection_exhaustion"}'
curl -X POST http://localhost:8090/faults/activate -H 'Content-Type: application/json' -d '{"scenario":"payments_errors","duration_seconds":120,"parameters":{"failure_rate":1}}'
curl -X POST http://localhost:8090/faults/deactivate -H 'Content-Type: application/json' -d '{"scenario":"payments_errors"}'
curl -X POST http://localhost:8090/faults/activate -H 'Content-Type: application/json' -d '{"scenario":"bad_deployment","duration_seconds":120,"parameters":{"delay_seconds":1.5}}'
curl -X POST http://localhost:8090/faults/deactivate -H 'Content-Type: application/json' -d '{"scenario":"bad_deployment"}'
curl http://localhost:8090/faults/status
```

| Scenario | Actual mechanism | Evidence and recovery |
| --- | --- | --- |
| F1: `db_connection_exhaustion` | Initial Orders inserts hold real leased PostgreSQL connections. Bounded pool max defaults to 4, acquire timeout 0.5s, hold 8s (range 0.1–15s). Does not modify PostgreSQL server connection limits. | Active/max approaches 1; wait/timeouts/5xx rise; DB acquire/query spans get long. DELETE/TTL wakes existing holders immediately. Ground truth honestly identifies **Orders DB connection-pool exhaustion**. |
| F2: `payments_errors` | Stable hash of the payment key selects failures with `failure_rate` 0–1 (default .9); `status_code` accepts only 503 or 402. Existing payment decisions remain idempotent. | Payment/checkout failures rise; reservations release; no injected sleep/timeouts; pool remains healthy. Clearing policy restores baseline. |
| F3: `bad_deployment` | Orders runtime release flag switches `v1` → `v2-buggy`. Pre-payment persistence adds 1–2s (default 1.5) and rejects even quantities with `OrderPersistenceCompatibilityError`. | `release_changed`, UTC timestamp, `service.version`, new `deployment.id`; slow/error `order.persistence` spans. Odd quantities are slow but can complete. Rollback wakes sleepers and restores `v1`. This is a runtime release simulation, not an actual container/Kubernetes rollout. |

OpenTelemetry resource version describes process boot (`v1`). Dynamic release version/deployment ID appear on inbound/workflow/persistence spans and normal release/request-completed log events. This avoids mutating immutable OTel resources. F3 does not introduce any scenario name into those signals.

## Automatic schedule

```sh
python scripts/faults.py auto-start --warmup 120 --duration 120 --recovery 90 --repeat 1 --seed 42
python scripts/faults.py auto-stop
curl -X POST http://localhost:8090/faults/auto/start -H 'Content-Type: application/json' -d '{"seed":42,"scenarios":["db_connection_exhaustion","payments_errors","bad_deployment"],"shuffle":false,"warmup_seconds":120,"duration_seconds":120,"recovery_seconds":90,"repeat":1}'
curl -X POST http://localhost:8090/faults/auto/stop
```

Auto mode is exclusive: a controller lock protects activation/deactivation and active schedules reject manual changes. `repeat` accepts 1–1000 cycles, order lists accept 1–30 entries, warmup/recovery 0–3600s, duration 0.1–1800s. Seeded shuffling is optional. Stop/shutdown cancels the schedule and best-effort clears the active target. A failed/unacknowledged activation keeps the controller locked until cleanup or target lease expiry. Restart clears both targets before accepting commands and marks unfinished journal sessions as interrupted; restart cleanup timestamps are not claimed to be the exact crash time.

## Observe all three signals

```sh
docker compose logs -f gateway orders inventory payments
curl http://localhost:8889/metrics
curl http://localhost:3100/ready
```

JSON stdout remains available during exporter downtime. OTLP exporters use bounded asynchronous queues, short network timeouts and background batch processors. The Collector routes traces to Jaeger's OTLP gRPC receiver, metrics to its Prometheus scrape exporter, and logs through `otlphttp/loki` → Loki's **native `/otlp`** path. There is no obsolete Loki exporter or extra forwarder. This follows [Grafana's native OpenTelemetry ingestion configuration](https://grafana.com/docs/loki/latest/send-data/otel/). Loki uses TSDB schema v13 and structured metadata.

Useful PromQL (wait roughly 6–10 seconds after traffic for exports/scrapes):

```promql
sum by (service_name) (rate(http_server_requests_total[1m]))
sum by (service_name) (rate(http_server_requests_total{http_status_code=~"5.."}[1m])) / sum by (service_name) (rate(http_server_requests_total[1m]))
histogram_quantile(0.95, sum by (le,service_name) (rate(http_server_duration_seconds_bucket[1m])))
db_pool_active{service_name="orders-service"} / db_pool_max{service_name="orders-service"}
rate(db_pool_timeouts_total{service_name="orders-service"}[1m])
histogram_quantile(0.95, sum by (le) (rate(db_pool_acquire_duration_seconds_bucket{service_name="orders-service"}[1m])))
sum by (outcome) (rate(payments_failed_total[1m]))
sum by (dependency) (rate(dependency_failures_total[1m]))
process_memory_usage_bytes
```

HTTP metric attributes use route **templates**, method and numeric status. Dependency metrics add a fixed service name. Database metrics use fixed operation names. Never label metrics with customer/order/request/trace IDs, scenario names or raw URLs. CPU time, memory and uptime are exported by each business process; restart counts can be inspected via `docker inspect` (no privileged host metrics collector is required).

LogQL in Grafana Explore:

```logql
{service_name="orders-service"} | json | level="ERROR"
{service_name="payments-service"} | json | event="payment_failed"
{service_name="orders-service"} |= "db_pool_timeout"
{service_name="orders-service"} |= "OrderPersistenceCompatibilityError"
{service_name=~"api-gateway|orders-service|inventory-service|payments-service"} |= "TRACE_ID"
```

In Jaeger choose `api-gateway` → `POST /api/orders`, or open `http://localhost:16686/trace/TRACE_ID` using a response/log trace ID. One successful checkout includes all four services, client/server spans and DB operations. Sampling defaults to 100%; `TRACE_SAMPLE_RATIO` adjusts parent-based head sampling.

## Evaluation isolation and data

`ground-truth/incidents.jsonl` has **one record per injection session**. It is written at activation with a null end, then updated atomically/fsynced at cleanup with `ended_at`, reason, parameters, target and expected signals. It survives controller restarts and is mounted **only** in the controller. Interrupted sessions are marked rather than silently lost.

Controller and traffic stdout are operational/evaluation data and are not exported over OTLP. Internal control routes are excluded from application middleware. The Collector also filters controller/traffic resource names defensively. OBSERVA should ingest only the four business-service OTLP streams and ordinary deployment metadata; do not give it the journal, controller API, controller stdout or Docker-wide logs. The internal control token protects target endpoints and never appears in application logs. Only the controller writes fault names/root-cause labels.

PostgreSQL and Loki/Prometheus retain data in Compose volumes; Prometheus retains 24h. Jaeger uses bounded in-memory storage (2,000 traces by default, configurable with `JAEGER_MAX_TRACES`), which is intentionally reset on restart. Its Go runtime has a 192 MiB soft memory budget within the 256 MiB container limit. The smaller trace count avoids memory pressure during continuous laptop runs; size retention and container memory together if increasing it. Loki's filesystem log storage grows until reset; stop/reset the demo after evaluation runs. Seed inventory is finite and can be reset reproducibly. No passwords, full request payloads or payment credentials are logged.

## Tests

Install the pinned development environment:

```sh
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
python -m pytest -q
python -m ruff check services controllers scripts tests
```

Default tests run without Docker: endpoint/validation contracts, full four-app ASGI workflow with in-memory repository doubles, trace/log/metric correlation, compensation, idempotency contracts, release rollback, controller conflicts, validation, TTL and seeded schedules. In-memory tests do not claim to validate PostgreSQL SQL/locks or the backend pipeline; live tests do that separately.

The unit suite also checks UI delivery, public-port configuration, and exclusion of static UI requests from business telemetry. Optional client-contract checks use Node's built-in test runner with no installed packages:

```sh
node --test tests/ui/client.test.mjs
```

Those checks cover a single checkout transmission, correlation IDs, translated payment failures, ambiguous network outcomes without automatic checkout retries, and safe rendering of trace metadata. Node is only needed for these optional development checks; the running interfaces require no Node process or frontend build.

With Compose running, use a quiet evaluation window (the traffic generator can remain active because stock assertions use `sku-005`). Against the live project this runs the non-fault live checks; the F1/F2/F3 fault scenarios are skipped unless `RUN_FAULT_TESTS=1` and `COMPOSE_PROJECT_NAME` names a throwaway project, because they strand legacy orders in the database they hit. See [Fault testing on a throwaway project](scripts/migrations/README.md#fault-testing-on-a-throwaway-project) for the full suite, and the [migration runbook](scripts/migrations/README.md) before applying schema migrations to the live databases:

```sh
RUN_LIVE_TESTS=1 TEST_COLLECTOR_OUTAGE=1 python -m pytest -q
```

PowerShell:

```powershell
$env:RUN_LIVE_TESTS='1'
$env:TEST_COLLECTOR_OUTAGE='1'
python -m pytest -q
```

The live suite creates real database rows, checks cross-database access denial and concurrency/idempotency, queries all three backends, tests the three incident signatures and recovery (throwaway project only), and validates journal isolation. The optional Collector outage test stops/restarts **this project's Collector**, verifying a checkout still succeeds. Run this suite sequentially; it exclusively controls faults and should not overlap a separate manual/automatic evaluation. Test endpoint defaults use the ports above; `TEST_GATEWAY_URL`, `TEST_CONTROLLER_URL`, `TEST_PROMETHEUS_URL`, `TEST_JAEGER_URL`, `TEST_LOKI_URL` can redirect backend queries. Direct-service database/health assertions use the default 8001–8003/8090 ports, so use default ports for the full acceptance run. Tests read the default mock DB password unless overridden in their environment.

## Configure, stop and reset

`.env.example` documents ports, mock credentials, URLs, sampling, pool/query/dependency bounds, traffic rate/seed/concurrency and controller parameter limits. `DATABASE_URL` is supplied separately to each service by Compose. If setting custom credentials in DSNs, URL-encode special characters. Restart/recreate containers after changing `.env`. Increasing traffic to 8 RPS can make pool exhaustion easier to inspect; keep it below the 50 RPS safety cap. Changing seed gives a different reproducible input sequence.

```sh
docker compose stop traffic-generator
docker compose start traffic-generator
docker compose down
# Explicitly deletes this demo's DB/telemetry volumes and isolated incident journal:
sh scripts/reset.sh
# Or PowerShell:
powershell -File scripts/reset.ps1
```

`docker compose down -v` resets DB/telemetry volumes but retains the host journal; reset scripts remove that journal as well. Keep it if evaluating completed runs. Stop automatic mode before shutdown for acknowledged cleanup; target TTLs remain the fallback after hard crashes. Heavy UI teardown: `docker compose --profile ui down`.
