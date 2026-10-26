# OBSERVA Dummy Application — Complete Implementation Specification

> **Purpose:** This document is a standalone, implementation-ready specification for an AI coding agent. Implement the complete dummy application described here without requiring the OBSERVA agentic system. The app exists to generate realistic, correlated **logs, metrics, and distributed traces** under normal traffic and controlled failure scenarios.

---

## 1. Goal and Boundaries

Build a small, runnable **microservices-based e-commerce simulation** used as a test environment for OBSERVA, an incident investigation and root-cause analysis system.

The application must:
- Run continuously and process synthetic business traffic.
- Produce structured logs for **all meaningful operations**, including successful operations, validation failures, service-to-service calls, database activity, retries/timeouts, fault activation/deactivation, and infrastructure lifecycle events.
- Export **metrics and distributed traces** alongside logs; all three signals should tell the same story about a given incident.
- Support **manual and automatic** injection of three independently controllable faults.
- Maintain separate *ground truth* records for evaluation; **never include ground-truth fault labels in the application telemetry consumed by OBSERVA**.
- Be easy to start, stop, inspect, test, and reset locally using Docker Compose.

### Explicitly out of scope

No real purchases, real payment provider, real shipping, customer accounts, login/authentication, message broker, Kubernetes, cloud infrastructure, LLM, RAG, or OBSERVA agents are required.

> **Scope reconciliation (2026-10-25).** The approved OBSERVA design adds a simulated, English LTR storefront with a **multi-item cart and demo checkout**, a **My Orders** view, and a separate evaluation-only **Operator Dashboard**. These are now **in scope** because they generate realistic, correlated traffic and telemetry. Everything else in this section stays out of scope. See [§12](#12-approved-design-reconciliation) for details. This supersedes the earlier "no browser UI, no shopping cart" wording.

---

## 2. Required Stack and Repository Layout

Use the following defaults unless the existing repository already uses an equivalent stack:

| Area | Implementation |
|---|---|
| Application services | Python 3.12 + FastAPI + Uvicorn |
| Inter-service requests | `httpx` with explicit timeouts |
| Database | PostgreSQL 16, with isolated schemas / ownership per service |
| Traces, metrics, logs | OpenTelemetry SDK + OTLP → OpenTelemetry Collector |
| Metrics backend | Prometheus (collector-exported scrape endpoint) |
| Trace backend | Jaeger (OTLP supported) |
| Logs backend | Loki (via Collector-supported pipeline or a dedicated log forwarder) |
| Orchestration | Docker Compose |
| Testing | pytest + httpx / integration tests |

**Implementation note:** If the chosen OpenTelemetry Collector distribution cannot export logs directly to Loki, use a supported path such as **Collector → OTLP-capable log receiver / Grafana Alloy → Loki**. Do not configure an exporter that the chosen image does not contain. Preserve JSON logs on stdout even when the telemetry pipeline is unavailable.

Suggested repository layout:

```text
dummy-app/
  README.md
  .env.example
  docker-compose.yml
  services/
    gateway/
    orders/
    payments/
    inventory/
    shared/                  # logging, tracing, metrics, client helpers
  controllers/
    traffic-generator/
    fault-injector/
  telemetry/
    otel-collector.yaml
    prometheus.yml
    grafana-alloy-config.*   # only if required by selected Loki integration
  scripts/
    init-db.sql
    smoke-test.sh
    reset.sh
  tests/
    unit/
    integration/
    scenarios/
  ground-truth/              # local mounted volume; NOT part of app telemetry
```

Use reproducible pinned image/library versions, health checks, `.env.example`, and README commands. Every process must work without OBSERVA being deployed.

---

## 3. Architecture and Business Request Flow

### Components

1. **API Gateway (port 8000):** Public entry point, validation/routing, request IDs, responses and latency measurement.
2. **Orders Service (8001):** Creates orders and coordinates inventory reservation and simulated payment; stores order state.
3. **Inventory Service (8002):** Checks/reserves stock and reports stock availability; owns inventory data.
4. **Payments Service (8003):** Simulates payment decisions and stores transaction status; no real processor.
5. **PostgreSQL:** One instance for local development, with **service-specific databases or schemas and credentials**; do not share tables across services. Use a bounded connection pool for Orders (and Inventory if needed).
6. **Traffic Generator:** Produces normal and harmless-noise traffic against the Gateway.
7. **Fault Injector:** Enables/disables faults via controller endpoints and optionally schedules them.
8. **OpenTelemetry Collector + backends:** Ingest application observability signals and make them queryable independently from OBSERVA.

### Typical transaction

```text
Traffic Generator
  → API Gateway: POST /api/orders
    → Orders: POST /orders
      → Inventory: POST /inventory/reservations
      → Payments: POST /payments/charge
      → Orders DB: persist final state
    ← Orders response
  ← Gateway response
```

Use one trace across the end-to-end transaction. Inject and extract W3C `traceparent` headers in outgoing/incoming HTTP requests. Preserve request IDs. Design sensible failure handling: inventory reservation must be released if payment fails (best-effort; record the attempt and result). Avoid silent success after a failed dependency.

### Required APIs

| Service | Endpoint | Behavior |
|---|---|---|
| Gateway | `GET /health` | Liveness/readiness-oriented response |
| Gateway | `POST /api/orders` | Forwards order creation |
| Gateway | `GET /api/orders/{id}` | Retrieves order status |
| Gateway | `GET /api/products` | Forwards inventory listing |
| Orders | `POST /orders` | Create and execute order workflow |
| Orders | `GET /orders/{id}` | Return stored order status |
| Inventory | `GET /inventory/products` | List seeded products |
| Inventory | `POST /inventory/reservations` | Reserve quantity; reject insufficient stock |
| Inventory | `POST /inventory/reservations/{id}/release` | Release reservation idempotently |
| Payments | `POST /payments/charge` | Simulated payment attempt with stable idempotency key |
| Each service | `GET /health` | Lightweight health endpoint |
| Fault Injector | `POST /faults/activate` | Activate one named fault and parameters |
| Fault Injector | `POST /faults/deactivate` | Deactivate named fault |
| Fault Injector | `GET /faults/status` | Current controller state (do not feed to OBSERVA) |
| Fault Injector | `POST /faults/auto/start` | Start configurable automatic schedule |
| Fault Injector | `POST /faults/auto/stop` | Stop automatic schedule and cleanup |

Example order request: `{ "product_id": "sku-001", "quantity": 2, "customer_id": "load-user-17" }`. Define and document complete JSON response schemas, status codes, validation behavior, and correlation fields in OpenAPI generated by FastAPI. Error bodies should be consistent: `error_code`, `message`, `request_id` (plus non-sensitive detail when appropriate).

---

## 4. Observability — Mandatory, Not Optional

**All services must export all three signals: logs, metrics, and traces. Logs alone do not satisfy this specification.**

### 4.1 Structured JSON logs

Write **one JSON object per line to stdout**, and export the logs to the telemetry pipeline. Common fields:

```json
{
  "timestamp": "2026-10-23T12:30:00.123Z",
  "level": "ERROR",
  "service.name": "orders-service",
  "environment": "local",
  "event": "dependency_request_failed",
  "message": "Payment dependency request failed",
  "trace_id": "32-character-lowercase-hex-trace-id",
  "span_id": "16charhexspanid",
  "request_id": "req-uuid",
  "operation": "POST /payments/charge",
  "dependency": "payments-service",
  "http.method": "POST",
  "http.route": "/payments/charge",
  "http.status_code": 503,
  "duration_ms": 806,
  "error.type": "PaymentServiceUnavailable",
  "order_id": "order-uuid"
}
```

Generate these event categories where applicable:
- Service startup/shutdown; readiness; unexpected exceptions.
- Request received, request completed, status, duration, route, request ID.
- Business operations: `order_created`, `inventory_reservation_started/succeeded/failed/released`, `payment_attempted/succeeded/failed`, `order_completed/failed`.
- Every outgoing dependency call: target service, method, route, duration, status, timeout, exception and retry attempt.
- DB calls (sampled where noisy): slow queries, pool acquisition wait, pool exhaustion, connection exceptions, transaction rollback.
- Controlled fault impact **as naturally observed by the service** (e.g., `db_pool_timeout` or `payment_declined`), not a root-cause answer.
- Traffic generator and fault controller emit **separate operational logs**, not mixed with the application logs used by OBSERVA.

Keep log levels meaningful: DEBUG (optional details), INFO (normal lifecycle), WARN (slow/expected recoverable problems), ERROR (failed business operations/dependencies). Never log passwords, tokens, payment credentials, or full sensitive payloads. Sanitize error text. Do not log every successful SQL statement if it overwhelms useful telemetry.

### 4.2 Metrics

Expose or export useful metrics with stable low-cardinality attributes: `service.name`, `http.route`, `http.method`, `http.status_code`, `dependency`, `outcome`, `scenario-independent` attributes. **Never use request_id, order_id, trace_id, customer_id, or raw URL as metric labels.**

Minimum instrumentation:
- Request count, error count/error ratio, latency histogram (for each service and route).
- Outbound dependency calls: calls, failures, timeouts, duration histograms.
- Orders created/completed/failed counters; payment success/failure counters.
- PostgreSQL client: active/idle/max pool connections, pool-acquire wait histogram, pool timeouts, DB query duration.
- Process/container-level resource metrics where feasible (CPU, memory, restart counts).
- Optional traffic-generator request rate and fault-controller state in **evaluation-only** metrics; do not leak fault labels into OBSERVA.

### 4.3 Distributed traces

- Span per inbound HTTP request, outgoing HTTP request, and meaningful database operation.
- End-to-end trace linking **Gateway → Orders → Inventory/Payments → DB**.
- Add span attributes (`service.name`, `http.route`, dependency, order workflow stage); set span status and record exceptions on failures.
- Avoid storing high-cardinality personal data or fault scenario labels in traces.
- Set trace sampling high enough to capture scenario activity in local testing (100% is acceptable for this small demo); make adjustable through env vars.
- Ensure `trace_id` in logs matches the associated trace and can be used to navigate from errors to traces.

### 4.4 Delivery and resilience

- Send OTLP telemetry to the Collector; wire Prometheus, Jaeger, and Loki through supported exporters/receivers.
- Provide service health/readiness checks and backend dashboards/queries showing logs, metrics, traces.
- Telemetry export must be **nonblocking and fail-open**: telemetry backend downtime must not break business operations. Local stdout logs must remain available.
- Exclude `/health` (or heavily sample it) from noise where reasonable, while keeping evidence of genuine readiness failures.

---

## 5. Normal Traffic and Harmless Noise

A load generator should automatically start with the environment and continuously send traffic to the Gateway at a configurable rate (default **3–5 requests/second**, adjustable). Include:

- Successful order creations and order lookups.
- Product listing and inventory checks.
- Expected validation failures (invalid quantity, unknown product).
- Occasional out-of-stock responses, deliberately declined synthetic payments, and harmless sporadic 4xx traffic.
- Concurrent requests to create meaningful contention without overwhelming the laptop.

Use deterministic random seeds for repeatability. Record traffic volumes and baseline latency/error metrics. Distinguish baseline noise from an incident via controlled rates and outcomes; ordinary noise must not include explicit fault scenario labels.

---

## 6. Failure Scenarios — Required Exact Semantics

Faults must be **independently triggerable**, reversible, observable, deterministic enough to test, and isolated to a clearly defined target. Default to only **one active scenario at a time** for clean evaluation. Reject conflicting activation with an informative error, unless explicitly opting into multi-fault tests later.

### F1 — Database Connection Exhaustion

**Root cause:** Orders Service's PostgreSQL connection capacity gets exhausted or held until new requests time out. This must be a **real controlled resource bottleneck**, not merely an HTTP 500 toggle.

**Injection:** Enable bounded connection pool with a small maximum; a controller-accessible fault setting causes designated Orders DB operations to hold leased connections for a configurable period while traffic continues. Enforce safe caps and timeouts; never change PostgreSQL system-wide connection limits unnecessarily.

**Expected evidence:**
- Pool active utilization approaches 100%; idle approaches zero.
- Pool acquire wait/timeout metrics spike and DB timeout/connection-acquire failure logs appear.
- Orders latency and 5xx increase; Gateway sees slow/error responses.
- Traces show long DB-acquire/query-related spans within Orders; Payments need not fail independently.
- Deactivation releases held capacity, with cleanup and recovery observable.

**Important:** A client-side pool saturation scenario is acceptable, but label it honestly as **Orders DB connection-pool exhaustion** in ground truth; do not claim the entire PostgreSQL server has exhausted all connections unless measured.

### F2 — Payments Errors

**Root cause:** Payments Service begins returning controlled failures on charge operations for a configurable proportion of traffic (default 80–100% during the fault).

**Injection:** Toggle a response failure policy at Payments; return 503 (dependency outage simulation) or consistent business failure as configured. Normal declined payments already exist as harmless baseline noise; use a sharply elevated failure rate during the incident.

**Expected evidence:**
- Payment failure rate rises; Orders fails dependent checkouts and releases reservations.
- Trace failures center on `POST /payments/charge` and propagate into Orders/Gateway.
- Overall latency stays approximately normal: **do not add artificial delay or timeouts** to this scenario.
- PostgreSQL pool remains healthy, isolating the problem from F1.
- After deactivation, success rate returns toward baseline.

### F3 — Bad Deployment

**Root cause:** A newly deployed version of **Orders Service** contains a regression affecting one operation, introduced at a definite deployment timestamp.

**Injection:** Simulate a version switch from `v1` to `v2-buggy` using a controlled runtime release flag **or** an actual container version rollout; expose release metadata (`service.version`, `deployment.id`) in normal service attributes/events. Version switch is enough for MVP if clearly documented; do not pretend it is a real Kubernetes deployment.

**Expected evidence:**
- Sudden failure/latency change at deployment timestamp (e.g., order persistence operation adds 1–2 seconds and a new exception occurs for certain requests).
- Distinct new error message/exception signature appears only on buggy version.
- Traces show a newly slow operation in Orders and associated elevated error rate.
- Rollback to `v1` restores original behavior.
- OBSERVA may infer temporal correlation with deployment via **normal release/deploy event telemetry**, but never receives the ground-truth scenario name.

### Scenario controller API payload example

```json
{
  "scenario": "payments_errors",
  "duration_seconds": 120,
  "parameters": { "failure_rate": 0.9 }
}
```

Allow equivalent scenario names `db_connection_exhaustion`, `payments_errors`, and `bad_deployment`; validate configurable values and maximum durations. `deactivate` must be idempotent. Fault state should expire automatically after a specified TTL. Controller must not require manual source-code edits or container restarts between scenarios.

---

## 7. Manual and Automatic Fault Modes

### Manual mode

Operators can activate/deactivate any one scenario via API or CLI, inspect status, and observe recovery. Include copy-paste `curl` commands and a minimal CLI wrapper if useful.

### Automatic mode

The controller runs a configurable deterministic schedule, for example:

1. Warmup: 120 seconds of healthy baseline traffic.
2. Activate one fault for 120 seconds.
3. Deactivate and allow 90 seconds of recovery.
4. Rotate through the next scenario; repeat or stop according to config.

Allow seed, scenario order/shuffle, warmup, duration, recovery and repeat settings. Auto and manual must not race: define an exclusive controller lock and return meaningful errors for incompatible commands. On shutdown or interruption, best-effort deactivate all active faults.

---

## 8. Ground Truth and Evaluation Isolation

Create `ground-truth/incidents.jsonl` containing one JSON record per injection session, for example:

```json
{
  "incident_id": "inc-uuid",
  "scenario": "payments_errors",
  "root_cause_service": "payments-service",
  "started_at": "2026-10-23T12:30:00Z",
  "ended_at": "2026-10-23T12:32:00Z",
  "parameters": { "failure_rate": 0.9 },
  "expected_signals": ["payment_failures", "orders_errors", "failed_payment_spans"]
}
```

This file belongs to **test/evaluation infrastructure**, not the business app. The OBSERVA investigation pipeline must have access only to logs, metrics, traces (and ordinary deployment metadata). It must **not** ingest fault-controller API state, evaluation-only metrics, or ground-truth records. App logs should report symptoms rather than strings like `fault_injected_payments_errors`.

---

## 9. Configuration and Deployment Requirements

- `docker compose up --build -d` starts all services, DB, traffic generator, controller, collector, and telemetry backends (with optional profiles for heavy UIs).
- `docker compose down -v` + reset script restores reproducible seeded data.
- All ports, DSNs, credentials, OTLP endpoint, request rates, timeouts, scenario durations and seeds documented in `.env.example`.
- Persistent volumes for PostgreSQL and optional telemetry data. Use mock credentials only and never commit secrets.
- Readiness dependency ordering; graceful signal handling; retries with backoff during boot.
- Small laptop-friendly resource limits and bounded concurrency.
- Provide useful README commands for: starting, checking service health, creating an order, viewing logs, inspecting metrics/traces, activating all three scenarios, entering auto mode, stopping/resetting.
- Make release metadata and timestamps UTC/ISO-8601; include `service.name`, `service.version`, `deployment.id` as OpenTelemetry resource or event attributes.

---

## 10. Tests and Acceptance Criteria

Implement automated tests, not just example scripts.

### Unit/contract tests
- Endpoint schemas, validation, error codes, idempotency (payments, inventory release), stock updates, order state transitions.
- Scenario API validation, TTL auto-expiry, conflict handling, reactivation/deactivation behavior.

### Integration and observability tests
- A normal checkout succeeds end-to-end.
- Each service emits parsable JSON logs with timestamp, level, service, event, request ID; traced requests include valid trace IDs and span IDs.
- A single checkout can be traced across Gateway, Orders, Inventory, Payments (and DB operations where present).
- HTTP/error/latency metrics exist and use bounded label cardinality.
- Telemetry pipeline accepts logs/metrics/traces; failure of the collector does not fail requests.

### Scenario tests
- **F1:** With concurrent load, pool saturation near capacity, elevated wait/timeout and increased upstream latency/errors occur; recovery follows deactivation.
- **F2:** Payment errors spike, Orders is impacted, payment spans fail, while typical request latency remains close to baseline and DB pool is healthy.
- **F3:** Version/deployment marker changes, new exception signature and slow Orders operation appear, then disappear after rollback.
- Normal traffic remains visible before/during/after fault; background harmless errors do not falsely look like the injected scenario.
- Each scenario can run independently, be stopped, and run again without corrupting state.
- Ground truth matches injection times and affected service, and does not appear in OBSERVA-facing telemetry.

### Definition of done

The project is complete when a developer can start it with Docker Compose, observe all **three telemetry signals** from each service, run each of the three scenarios manually or automatically, verify characteristic telemetry signatures, and compare resulting incidents against an isolated ground-truth file **without implementing OBSERVA itself**.

---

## 11. Instructions to an AI Coding Agent

**Implement the actual runnable repository, not a conceptual demo or only documentation.** Work in this order:

1. Scaffold services, configs, Compose, health checks, DB schemas and sample product data.
2. Implement working order/inventory/payment flow, failure-safe state changes, and API tests.
3. Add consistent OpenTelemetry instrumentation (JSON logs, metrics, distributed traces) and verify collection/query path.
4. Add continuous baseline/noise traffic generator.
5. Implement the three reversible fault scenarios and the controller with manual/auto modes and TTL cleanup.
6. Add isolated ground-truth recording, reproducible tests, and README runbook.
7. Run all available checks, fix failures, and report exact commands, observed telemetry and any limitations.

**Non-negotiable:** Do not invent observed results, do not leak ground-truth labels into OBSERVA-facing data, do not implement OBSERVA agents, and do not silently omit logs/metrics/traces or any of the three fault scenarios. If an existing repository conflicts with the defaults in Section 2, preserve its established conventions while meeting all behavior and acceptance criteria above.

---

## 12. Approved Design Reconciliation

This section records how the approved design (39 artboards, 2026-10-25) changes this spec. It describes target behaviour for later phases. The code in this commit is unchanged.

### 12.1 In scope
- **Storefront** (English LTR): product collection with availability, multi-item cart with server-computed quote, and a demo checkout. The checkout collects only a demo name and email, which are validated and **not persisted, logged or traced**, plus one simulated payment method. It has these outcomes: processing, success, payment failed (retryable), not placed (unavailable item, not retryable) and status unknown, plus My Orders (list and detail).
- **Operator Dashboard**, served only by the fault controller: manual F1/F2/F3 configuration, automatic cycle, recovering, controller disconnected and fault-status-unknown states. Service health comes from real probes (for the services the controller shares a network with: Orders and Payments) and Prometheus metrics (all services; missing or stale evidence is `unknown`, never healthy), never from fault state.
- **Still out of scope:** real payments, shipping, accounts and authentication.

### 12.2 Checkout correctness requirements
1. **Persist the attempt before side effects.** A client-generated attempt reference (`Idempotency-Key`) is stored with a request hash, before inventory or payment calls. A replay with the same key returns the same result; the same key with a different request is a conflict.
2. **No false failures.** A payment 503, a timeout, or a missing record is **never** proof of failure. These move the attempt to an *unknown* state, which is resolved only by a fence:
   - a payment void tombstone, after which a late charge with the same key is refused;
   - an attempt "resolve" tombstone, after which a late checkout with the same key is refused.
3. **Safe compensation.** Inventory is released only after payment is confirmed declined or voided. A confirmed charge always completes the order, never releases it.
4. **Reconciliation.** A bounded background reconciler resumes non-terminal attempts, including after a restart, with backoff. It logs *stuck* attempts instead of guessing.

### 12.3 Preserved invariants
- All three signals from every service; the three fault scenarios (F1 Orders DB pool exhaustion, F2 payment errors with no added latency, F3 bad deployment of the Orders service) and their expected evidence.
- Ground truth stays isolated from customer-facing assets and from OBSERVA-facing telemetry.
- Metric labels stay low-cardinality: no attempt references, SKUs or contact data.
