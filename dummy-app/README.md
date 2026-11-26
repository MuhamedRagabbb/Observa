# Checkout application

A local checkout system for testing incident detection. The gateway, orders, inventory and payments services emit structured logs, metrics and distributed traces. A traffic generator drives checkouts, and the operator can inject a fault and watch the services recover.

## Start

From `dummy-app/`, with Docker Compose v2:

```sh
docker compose up --build -d
docker compose ps -a
```

Compose starts PostgreSQL, applies schema migrations and then starts the checkout services. An `Exited (0)` status for `db-migrate` means migrations finished successfully. If setup fails, inspect `docker compose logs db-migrate`. Traffic starts automatically at four requests per second by default.

| Open locally | Purpose |
| --- | --- |
| [Storefront](http://127.0.0.1:8000/) | Browse products, place a checkout and inspect the order |
| [Operator](http://127.0.0.1:8090/) | Inject a fault, schedule a cycle or check recovery |
| [Grafana dashboard](http://127.0.0.1:3000/d/observa-overview) | Compare metrics and logs and follow a trace |
| [Prometheus](http://127.0.0.1:9090/) and [Jaeger](http://127.0.0.1:16686/) | Query metrics and inspect distributed traces |
| [Gateway API](http://127.0.0.1:8000/docs) and [controller API](http://127.0.0.1:8090/docs) | Browse endpoints and request schemas |

Compose binds published ports to loopback. Use [.env.example](.env.example) to customize ports, credentials, sampling or traffic. On Linux, set `LOCAL_UID` and `LOCAL_GID` to the account that owns the bind-mounted journal. On Windows, the Docker Desktop defaults work. To follow a fault through detection and reporting, start the other two Compose projects in the [Observa quickstart](../README.md#run-locally).

## Fault experiments

In the operator, use **Inject fault** for one scenario or **Start cycle** for a sequence with warmup and recovery periods. The F1–F3 codes **in the operator** identify fault scenarios. In the Observa pipeline those codes identify detection, investigation and correlation.

| Operator scenario | Mechanism | What to look for |
| --- | --- | --- |
| F1: `db_connection_exhaustion` | Orders holds database connections, by default for 8 seconds. | Pool utilization approaches 100%, acquisition timeouts rise and callers wait or fail. |
| F2: `payments_errors` | A configurable share of payment attempts returns 503 or 402. | Failed charges at Payments and failed requests from Orders. A 402 is a decline and can produce a different set of alerts from a 503. |
| F3: `bad_deployment` | Orders switches to the simulated `v2-buggy` release. Persistence slows by 1–2 seconds and fails for even quantities. | Look for a release change, slow persistence spans and `OrderPersistenceCompatibilityError`. |

Alternatively, run the standard-library Python CLI from this directory. A manual fault stops when its duration expires. An active cycle owns the controller until the cycle ends or is stopped.

```sh
python scripts/faults.py activate payments_errors --duration 120
python scripts/faults.py status
```

To stop a manual fault early, use `python scripts/faults.py deactivate payments_errors`. To schedule all three experiments, run:

```sh
python scripts/faults.py auto-start --warmup 120 --duration 120 --recovery 90 --repeat 1 --seed 42
```

Stop the schedule with `python scripts/faults.py auto-stop`. The controller exposes the active fault at `/faults/status`, service readings at `/services/health` and recent operator actions at `/activity`.

## Checkout and traffic

The storefront supports a multi-item cart, order history and recovery of a checkout whose response was lost. Orders, Inventory and Payments each own a PostgreSQL database. Payments uses idempotency keys and Inventory reserves stock per order. If a payment fails, Orders attempts to release the reservation. Check the order state before repeating an ambiguous checkout.

The default `TRAFFIC_FLOW=legacy` sends single-item orders, product reads and lookups. Set `TRAFFIC_FLOW=checkout` to exercise multi-item carts, idempotency replays and order history. `TRAFFIC_SEED` controls the generated input sequence. The traffic generator leaves `sku-005` available for integration tests.

## Inspect telemetry

The Collector sends logs to Loki, traces to Jaeger and exposes metrics for Prometheus. Query the four business services when investigating a failure. The controller stores experiment labels and times in `ground-truth/incidents.jsonl` for evaluating results separately. The traffic generator and controller logs are excluded from application telemetry.

Example PromQL queries:

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

LogQL in Grafana Explore:

```logql
{service_name="orders-service"} | json | level="ERROR"
{service_name="payments-service"} | json | event="payment_failed"
{service_name="orders-service"} |= "db_pool_timeout"
{service_name="orders-service"} |= "OrderPersistenceCompatibilityError"
{service_name=~"api-gateway|orders-service|inventory-service|payments-service"} |= "TRACE_ID"
```

Use a log's `trace_id` to open the request in Jaeger. Prometheus retains 24 hours of metrics. Jaeger keeps up to 2,000 traces in memory by default and clears them on restart.

## Isolated Docker preview

Use the preview to exercise the UI or run fault tests alongside the normal stack. It has separate ports, volumes, an image tag and a journal. From `dummy-app/` in PowerShell:

```powershell
Remove-Item Env:COMPOSE_PROJECT_NAME -ErrorAction SilentlyContinue
$preview = '-f','docker-compose.yml','-f','docker-compose.preview.yml'
docker compose @preview up --build -d --wait postgres gateway orders inventory payments fault-injector collector prometheus jaeger loki telemetry-health
```

Open the [preview storefront](http://127.0.0.1:18000/) or [preview operator](http://127.0.0.1:18090/). To generate traffic, run `docker compose @preview --profile traffic up -d --no-deps traffic-generator`. To stop the preview and retain its volumes, run `docker compose @preview --profile traffic down`. For React development, run `npm ci` in `web/`, then `npm run dev:store` or `npm run dev:operator`.

## Tests and maintenance

From `dummy-app/`, create a Python 3.12 virtual environment, activate it and install the development dependencies:

```sh
python -m venv .venv
# PowerShell: .\.venv\Scripts\Activate.ps1
# POSIX: source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

Use `npm run verify` in `web/` for React type checks and unit tests. After an API change, run `python -m scripts.export_openapi` from `dummy-app/` and `npm run gen:api` from `web/`.

- [Browser tests](web/e2e/README.md) cover the storefront and operator with fixtures, the real controller or an isolated preview.
- [Startup checks](tests/startup/README.md) exercise migrations, a fresh checkout and restart behaviour.
- [Database migrations and fault tests](scripts/migrations/README.md) cover backups, schema upgrades and tests against disposable databases. If you override ports in `.env`, export the matching test URLs and database port before running live tests.
- [Implementation specification](IMPLEMENTATION_SPEC.md) describes the service APIs and telemetry contract. [Validation notes](VALIDATION.md) document the integration checks.

To pause background requests, run `docker compose stop traffic-generator`. Resume with `docker compose start traffic-generator`. To shut down the local environment and retain its database and telemetry volumes, run `docker compose down`.
