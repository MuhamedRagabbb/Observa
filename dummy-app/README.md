# OBSERVA dummy commerce environment

A runnable local test bed generating correlated JSON logs, OpenTelemetry metrics and distributed traces. Four Python 3.12/FastAPI services use PostgreSQL 16, with separate databases and credentials. Includes reproducible traffic, three independent reversible failures, an English storefront and a separate evaluation-only React operator interface at `/`, deployed live in P5-G Stage 3. No OBSERVA agents are included.

See [VALIDATION.md](VALIDATION.md) for the recorded commands, observed live telemetry, acceptance results and practical limits of the first implementation run (2026-10-24; it predates the React UIs).

> **Location:** this app lives in `dummy-app/` inside the OBSERVA repository. The project overview is in the [root README](../README.md). Run every command below from `dummy-app/`. The English storefront, multi-item cart and operator redesign are specified in [IMPLEMENTATION_SPEC.md §12](IMPLEMENTATION_SPEC.md#12-approved-design-reconciliation).
>
> **Working on a feature such as F1 Alert Detection & Grouping?** Read [For OBSERVA feature teams](#for-observa-feature-teams-eg-f1-alert-detection--grouping). Note that "F1/F2/F3" below are the three injected *fault scenarios* of this test bed, not the OBSERVA features F1-F7.

## Project status

| Area | Status |
| --- | --- |
| Backend: multi-item checkout, My Orders, checkout reconciler (off by default), controller status / service health / activity, browser request guard | Done and tested (P0-P2, P5-0) |
| React storefront, served at `http://localhost:8000/` | Done and user-approved (P3-P4) |
| React operator, at `http://localhost:8090/` in this code (status and experiment banner, service health and links, manual faults, automatic cycle, phone layouts) | Built and tested against mocks (P5-A to P5-E); P5-E awaits formal acceptance |
| Operator against the **real controller** (browser contract tests and one real fault on an isolated preview) | **Verified.** Browser contract tests passed (P5-F); T2 on the isolated preview passed in P5-G Stage 2 |
| Operator at `http://localhost:8090/`; old `/ui/next/` page links redirect | **Deployed live (P5-G Stage 3).** Rollback uses `observa-dummy:rollback-a7db314` |
| Legacy operator files removed; operator Grafana link opens the OBSERVA dashboard | **Done in code (P5-G Stage 4); deployment pending.** The live image still links Grafana to its Home page |
| Final docs reconciliation and validation | Pending (P6) |

A long-running stack built from an older image can still show the Arabic storefront at `:8000/`; a fresh `docker compose up --build` from this code serves the React storefront.

> **Live tests and host ports:** the tests read ports such as `POSTGRES_PORT` from the shell environment, not from `.env`. If you changed a port in `.env`, for example because another PostgreSQL already uses 5432, export the same value before running `pytest`.

## Start

Install Docker Desktop with its Linux engine running (or Docker Engine + Compose v2+). From this directory:

```sh
cp .env.example .env
docker compose up --build -d
docker compose ps
python scripts/smoke.py
```

On PowerShell, use `Copy-Item .env.example .env`. Prerequisites: Docker with Compose v2 (and network access for the first build); Python 3.12 on the host only for scripts and tests; Node 24 or newer on the host only for developing the React UIs in `web/` (the image builds them in its own Node stage). All services run in containers. Images and Python dependencies (including transitive dependencies) are pinned. Allow several minutes for the first download/build. Allocate roughly 2–3 GB to Docker. Container memory/concurrency limits keep this small enough for a laptop.

On Linux, set `LOCAL_UID`/`LOCAL_GID` in `.env` to your `id -u`/`id -g` so the controller can write the bind-mounted `ground-truth` directory. Windows Docker Desktop works with the defaults.

Traffic begins automatically at 4 requests/second with seed 42. All exposed ports bind to `127.0.0.1`. These mock credentials and unauthenticated local business/controller APIs are for local testing.

| Entry point | Default address |
| --- | --- |
| Storefront (English, React) | http://localhost:8000/ |
| Operator, React (evaluation only) | http://localhost:8090/ (`/ui/next/` redirects) |
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

Open http://localhost:3000/d/observa-overview (anonymous viewing enabled).

The telemetry stack as configured: services export OTLP to the Collector (`:4317` gRPC / `:4318` HTTP); **metrics** go to the Collector's Prometheus endpoint (`:8889`), which Prometheus scrapes every 3 s and keeps for 24 h; **logs** go to Loki; **traces** go to Jaeger (all-in-one, in-memory, 2,000 traces, reset on restart). Grafana only reads these three. There is **no Tempo, no Alertmanager and no Prometheus alert or recording rules**: alert detection is not provided by this test bed.

`telemetry-health` checks the real readiness endpoints for Collector, Prometheus, Jaeger and Loki; application health checks include database readiness. Applications start and continue working without healthy telemetry backends.

## Business API

The storefront at `http://localhost:8000/` is the English React app built from `web/` (P4). It lists live products with search, keeps a multi-item cart, places checkouts through `POST /api/checkout` (one POST per attempt, never retried automatically; an unclear answer is shown as "Status unknown" with Check status), and lists the browser's orders under **My Orders**, merged with any attempt that is still unconfirmed. The browser keeps only an anonymous customer ID, the cart, unconfirmed attempts and, for a day, a "Not placed" reminder for an attempt that Check status found was never placed (the server does not list such attempts; the reminder is display only and never an order); contact details are never stored. All transaction state remains owned by the backend databases. Prices are illustrative USD from the integer-cent API values; no real charge is made.

The Gateway serves the storefront's entry page only at an explicit allowlist: `/`, `/checkout`, `/orders` and `/orders/<OBS-… or ATT-… reference>`, for GET and HEAD. There is no catch-all: any other path (an unknown route, `/unavailable`, a typo) is a plain, instrumented 404. Only those page requests and `/ui/` files are left out of application telemetry, and only on the Gateway; the orders service's own `/orders` API is unaffected. The bundle's files stay at `/ui/next/assets/`, and an old `/ui/next/<page>` link redirects (308) to `/<page>`. The legacy Arabic storefront was removed in P4-H.

In this code, the React operator is at `http://localhost:8090/` and old `/ui/next/` page links redirect there (308, query kept). Only `/` is an operator page, for GET and HEAD; other page paths return 404. It calls the controller on its own origin. T2 on the isolated preview passed in P5-G Stage 2, and Stage 3 deployed the React operator live. The operator is evaluation infrastructure and must not be scraped or ingested by the investigating agent.

P5-G Stage 4 removed the legacy operator files; rollback uses the Docker tag `observa-dummy:rollback-a7db314`. `/ui/assets/*` still returns 404. The React bundles are built in the image's Node stage, below. Storefront and operator pages use separate origins, and the storefront does not proxy fault-control calls through Gateway. Static UI requests and `/ui/config` are excluded from application telemetry, while shopping API requests retain normal instrumentation. Public navigation links follow the Compose port settings through a non-sensitive UI config endpoint. In this code the operator's Grafana link opens the OBSERVA dashboard at `/d/observa-overview` (pending deployment: the live image still opens Grafana Home); Loki opens Grafana Explore. All mock-credential and loopback-only boundaries still apply.

### React UIs (`web/`)

The React interfaces are built in `web/`. In this code, the storefront's pages are at `http://localhost:8000/` (see above) and the operator page is at `http://localhost:8090/`, with `/ui/next/` redirecting to `/`. Both bundles' files are served under `/ui/next/` on their own origin, each origin serves only its own build, and `/ui/` requests are excluded from application telemetry. T2 passed in P5-G Stage 2; Stage 3 deployed this operator routing live.

The Dockerfile builds both bundles in a Node stage (`npm ci`, typecheck, `npm run build`) and copies only `web/dist` into `services/ui/dist` of the Python runtime image; Node does not ship. A running stack gets this routing only after its image is rebuilt from this code and deployed. Image builds, including `docker compose build` and `up --build`, now pull `node:24` and reach the npm registry, so the first build is slower and needs network access. Compose builds every app service as `observa-dummy:local`, so a Compose build replaces the image a running stack uses, and recreating the stack deploys it. To check an image without touching a running stack, build it under its own tag and run the opt-in image tests. They use throwaway containers with no network:

```bash
docker build -t observa-dummy:p3-verify .
RUN_IMAGE_TESTS=1 python -m pytest -q tests/image   # IMAGE_UNDER_TEST overrides the tag
```

Without Docker, build in `web/` (`npm ci`, then `npm run build`) and run the Gateway or controller from the repository with `UI_BUNDLE_DIR` set to the absolute path of `web/dist`. The default is `services/ui/dist`. `web/` has its own checks: `npm run verify` runs the typecheck, the generated API type check and the tests. After an API change, run `python -m scripts.export_openapi`, then `npm run gen:api` in `web/`.

### Storefront preview (isolated from the live stack)

Use one of these to review the React storefront and operator. None of them touches the live `observa-dummy` project or the `observa-dummy:local` image.

1. **Mocked browser tests and visual review:** in `web/`, run `npm run e2e`, then `npm run e2e:review`. Screenshots at 1440, 390 and 375 sit beside the approved artboards. Details: `web/e2e/README.md`.
2. **A throwaway preview project:** `docker-compose.preview.yml` is used on top of `docker-compose.yml`. It sets:
   - its own project name, `observa-p4-preview`;
   - its own image tag, `observa-dummy:p4-preview`;
   - its own host ports: Gateway 18000, controller 18090, Prometheus 19090, Jaeger 26686, Grafana 13000;
   - its own journal folder, `.preview/ground-truth`.

   Background traffic starts only with `--profile traffic`, and no preview container restarts by itself. `tests/unit/test_preview_compose.py` checks all of this. Never add `-p` or `COMPOSE_PROJECT_NAME`.

   ```powershell
   Remove-Item Env:COMPOSE_PROJECT_NAME -ErrorAction SilentlyContinue
   $f = '-f','docker-compose.yml','-f','docker-compose.preview.yml'
   docker compose @f up --build -d --wait postgres gateway orders inventory payments fault-injector collector prometheus jaeger loki telemetry-health
   foreach ($svc in 'inventory','payments','orders') {   # the checkout migrations, as in runbook step 6
     docker compose @f exec -T $svc sh -c "set -f; python -m services.shared.migrations --dsn `${DATABASE_URL}?lock_timeout=5s --dir scripts/migrations/$svc"
   }
   docker compose @f --profile traffic up -d --no-deps traffic-generator   # optional background checkout traffic
   # From this code: Storefront http://127.0.0.1:18000/   Operator http://127.0.0.1:18090/
   docker compose @f down   # containers and networks only; remove observa-p4-preview_* volumes by exact name
   ```
3. **Hot reload:** in `web/`, `npm run dev:store` (http://127.0.0.1:5180/) or `npm run dev:operator` (:5181). These proxy to the preview project. `OBSERVA_GATEWAY_URL` and `OBSERVA_CONTROLLER_URL` change the target, and the live ports 8000 and 8090 are refused.

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

**My Orders** (multi-item checkouts, after orders migrations 001-003): `GET /api/orders?customer_id=web-user-1&limit=20` returns that customer's checkouts newest first, at most `limit` (1-50, default 20; there are no older pages). Each entry is the same view as `GET /api/attempts/{attempt_ref}?customer_id=…`, which serves the order details: `attempt_ref`, `order_number`, `state`, `outcome`, `retryable`, `items` (`product_id`, `quantity`, `unit_price_cents` once priced), `total_cents`, `error_code` and `created_at`. Product names come from `/api/products`. Legacy single-item orders are not listed, and `GET /api/orders/{order_id}` keeps serving them unchanged.

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

`TRAFFIC_FLOW` selects the mix. `legacy` (the default, and what the live project runs) is the single-item mix above, byte-for-byte unchanged. `checkout` (needs orders migrations 001-003) sends multi-item carts through `POST /api/checkout` (~55%: 1-3 distinct products from `sku-001`-`003`, 1-3 units each, so F3 hits carts whose total units are even), controlled idempotency replays of checkouts that already had a definite result (201, 402 or 409: ~5% exact, which must return the same result without a second charge; ~1% same key with a changed cart, which must return 409), the same noise as before (decline customers, `sku-004`, unknown product, zero quantity, missing `Idempotency-Key`), attempt lookups, My Orders, quotes, catalog reads and a missing route. Attempt references are `ATT-<run prefix>-NNNN`; `TRAFFIC_RUN_PREFIX` (0-65535) pins the prefix for a repeatable run, otherwise it is random so a restart never replays the previous run. Every checkout-mix choice and cart is derived from the seed and the request's sequence number, so response timing does not change them; only which answered checkout a replay or lookup targets follows response order. `sku-005` stays free of background traffic in both flows.

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

## Operator API (controller)

Evaluation-only, served by the controller on `:8090`. Operator-facing responses never include the journal's ground truth (`root_cause`, `root_cause_service`, `expected_signals`); those stay in the incident journal.

```sh
curl http://localhost:8090/faults/status       # state, active fault, cycle phase, safe controls
curl http://localhost:8090/services/health     # probes + Prometheus per business service
curl "http://localhost:8090/activity?limit=20" # this controller session's activity, newest first
```

- **Which browser requests it answers** (`controllers/fault_injector/guard.py`): the controller has no login and trusts its loopback binding. Every request must name a loopback `Host` (`127.0.0.1`, `localhost` or `[::1]`, any port), so a page that rebinds its own name to 127.0.0.1 can neither read nor change anything. A state-changing request (`POST`/`PUT`/`PATCH`/`DELETE`) that carries `Origin` must come from exactly `http://<Host>` (`null` or any other origin is refused), and a `Sec-Fetch-Site` other than `same-origin` or `none` is refused. Otherwise the answer is 403 (`host_not_allowed`, `origin_not_allowed` or `cross_site`) before anything runs. `curl`, `scripts/faults.py` and test clients send no `Origin` and are unaffected; the operator page sends its own origin. `X-Forwarded-*` and `Forwarded` are never read, and there is no CORS. This stops browsers, not programs that can reach the port (any Host can be sent): the 127.0.0.1-only publishing and the Docker networks are the boundary for those. The operator dev server (`npm run dev:operator`) rewrites only its own page's `Origin` (`http://127.0.0.1:5181`) to the controller's; open it at that address, not `localhost:5181`, or its changes are refused.
- `/faults/status` keeps `active` and `auto_running` and adds:
  - `state`: `idle`, `manual`, `automatic`, `recovering` or `unknown`. `unknown` means a control was not acknowledged by its target (`activation_unconfirmed` / `deactivation_unconfirmed`); the target's lease still ends the fault, and retrying the stop is the safe action.
  - `auto`: `phase` (`warmup`, `fault`, `recovery`, `stopping`), `repeat`/`repeats`, `step`/`steps`, `scenario`, `phase_started_at`, `phase_ends_at`, `seed`, `shuffle` and the cycle config.
  - `active.expires_at`, `last_fault` (`scenario`, `ended_at`, `end_reason`), and `now` (server clock, for countdowns).
  - `controls`: for `activate`, `deactivate`, `auto_start` and `auto_stop`, whether it is safe now and why not. After a fault the controller reports `recovering` until a health check taken after the fault finds every service healthy (at most 180 s).
  - The server enforces the same rules: a manual activation or a new cycle is refused with 409 `status_unknown` while a control is unconfirmed and with 409 `recovering` while services recover (plus the existing `fault_conflict`, `auto_running` and `controller_busy`). Stops, retried stops, `auto/stop`, lease expiry, restart cleanup and the running cycle's own faults are never refused.
- `/services/health` checks every 5 s (`HEALTH_INTERVAL_SECONDS`). Orders and Payments, which share the controller's `control` network, get a `/health` probe; every service is judged from its 5xx share, p95 and mean latency over the last 30 s in Prometheus. States: `unavailable` (probe failed), `unknown` (no trustworthy evidence: Prometheus unreachable, its Collector scrape down, the service never seen, or, for the unprobed Gateway and Inventory, no requests in the window, because a stopped service's series linger in the Collector for minutes), `degraded` (5xx share >= 5%, or p95 or mean latency >= 1 s) or `healthy`. It is never derived from fault state, and missing data is never reported as healthy.
- Duration histograms (`http.server.duration`, `dependency.duration`, `db.query.duration`, `db.pool.acquire.duration`) are recorded in seconds with second-scale buckets: 0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 0.75, 1, 2.5, 5, 7.5, 10, 15 and 30 s (fixed in P2-C; before that the SDK's millisecond defaults put every request in the first bucket and p95 read about 4.75 s). Metric names are unchanged; the Grafana p95 panel and the PromQL below read them as before.
- `/activity` keeps the last 200 entries in memory: requested, acknowledged, unconfirmed and stopped faults, rejected controls, cycle start/phase/stop/finish, and controller start.
- Network access: the controller joins `control` (shared only with Orders and Payments: their control endpoints and `/health`) and `observability` (read-only Prometheus queries). It is not on `business`, cannot reach the Gateway, Inventory or PostgreSQL, and still has no OTLP exporter.

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

## For OBSERVA feature teams (e.g. F1 Alert Detection & Grouping)

**Naming:** in this README F1 `db_connection_exhaustion`, F2 `payments_errors` and F3 `bad_deployment` are the test bed's injected *fault scenarios*. The OBSERVA features F1-F7 (for example the branch `F1_Intelligent-Alert-Detection-&-Grouping`) are separate work that consumes this test bed's telemetry.

**Run it and make an incident** (Docker; the faults are real but affect only your own local stack):

```sh
cp .env.example .env
docker compose --profile ui up --build -d     # Grafana is optional
python scripts/smoke.py                       # health and a sample checkout
python scripts/faults.py activate payments_errors --duration 120
python scripts/faults.py deactivate payments_errors
```

**What a feature may read** (read-only, local HTTP; the four business services are `api-gateway`, `orders-service`, `inventory-service`, `payments-service`):

| Signal | Where | Contract |
| --- | --- | --- |
| Metrics | Prometheus `:9090` (PromQL above) | e.g. `http_server_requests_total`, `http_server_duration_seconds_bucket`, `dependency_failures_total`, `db_pool_active` / `db_pool_max`, `db_pool_timeouts_total`, `payments_failed_total`; labels `service_name`, `http_route`, `http_status_code`; durations in seconds |
| Logs | Loki `:3100` (`{service_name="orders-service"} \| json`) | one JSON object per line: `timestamp`, `level`, `service.name`, `event`, `message`, `trace_id`, `span_id`, `request_id`, `operation`, `dependency`, `http.route`, `http.status_code`, `duration_ms`, `error.type`; events such as `request_completed`, `dependency_request_failed`, `payment_failed`, `db_pool_timeout`, `release_changed`, `order_reconciliation_required` |
| Traces | Jaeger `:16686` (its UI and query API) | one trace per request across Gateway, Orders, Inventory, Payments and DB spans; `trace_id` matches the logs |

The full contract is [IMPLEMENTATION_SPEC.md §4](IMPLEMENTATION_SPEC.md#4-observability--mandatory-not-optional) (logs, metrics, traces) and §6 (what each fault produces). An alert or grouping feature must **not** read the controller (`:8090`), `ground-truth/incidents.jsonl` or the controller/traffic logs: those are the answer key, to be used only offline to score results. This test bed ships no alerts, so detection rules are the feature's own work.

**Develop before integrating:**

- *With fixtures (no Docker):* the log line shape in spec §4.1, the PromQL above and the per-fault signatures in the table under Manual failures are enough to write fixtures and unit-test grouping logic (F1 pool exhaustion: Orders `db_pool_timeout`, pool utilisation near 1, Orders 5xx; F2: payment failures and 5xx with a healthy pool; F3: Orders latency up by 1-2 s, `release_changed`, `OrderPersistenceCompatibilityError`). No recorded telemetry captures are committed yet; once a stack runs, save small query results (a `query_range` JSON, a LogQL JSON, a Jaeger trace JSON) in your feature branch.
- *Needs the real stack:* scrape and export delays (wait about 6-10 s after traffic), Loki structured metadata, the volatile Jaeger store, behaviour during a Collector outage, and the exact numbers an incident produces.
- The generated HTTP API snapshots are `web/openapi/gateway.json` and `web/openapi/controller.json` (refresh with `python -m scripts.export_openapi`); they matter only to API clients, not to telemetry consumers.

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

The unit suite also checks UI delivery, public-port configuration, and exclusion of static UI requests from business telemetry. React client checks run with `npm run verify` in `web/`. Node is only needed on the host for `web/` development and checks; the running containers include no Node process (the React bundles are built in the image's Node stage).

The Operator Dashboard's contract tier uses the **real controller app without Docker**: `tests/harness/operator_contract.py` binds it on `127.0.0.1:18190` beside stub targets and test control (`127.0.0.1:18191`). Settings are validated before startup; live/preview ports and inherited proxy or service settings are refused. The reported Gate A A2 test exposed a redirect-isolation failure: Chromium followed a fulfilled 302 to the harness sentinel before the audit detected it. The revised fixture refuses reissued redirects and installs a page-scoped Chromium network allow-list. Redirect decisions and pattern semantics have unit coverage, and the Chromium enforcement was verified by the Gate A retest and the full T1 matrix (1440, 390 and 375, run twice); popups, dedicated workers and out-of-process iframes have documented limits. Live and preview destinations are checked as patterns only, never contacted by these tests. The permitted unit tests open no sockets. See `web/e2e/README.md` ("P5-F") for the failure, scope limits and results.

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

The live suite creates real database rows, checks cross-database access denial and concurrency/idempotency, queries all three backends, tests the three incident signatures and recovery (throwaway project only), and validates journal isolation. The optional Collector outage test stops/restarts **this project's Collector**, verifying a checkout still succeeds. Run this suite sequentially; it exclusively controls faults and should not overlap a separate manual/automatic evaluation. Test endpoint defaults use the ports above; `TEST_GATEWAY_URL`, `TEST_ORDERS_URL`, `TEST_INVENTORY_URL`, `TEST_PAYMENTS_URL`, `TEST_CONTROLLER_URL`, `TEST_PROMETHEUS_URL`, `TEST_JAEGER_URL`, `TEST_LOKI_URL` and `POSTGRES_PORT` redirect them, so a throwaway project can run on other host ports alongside the live one (see the migration runbook). Tests read the default mock DB password unless overridden in their environment.

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
