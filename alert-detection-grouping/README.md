# Observa — Alert Detection & Grouping (F1)

F1 reads metrics from Prometheus, logs from Loki and traces from Jaeger. It detects sustained problems, groups related alerts into incidents and saves the evidence for F2. Detection runs independently of Grafana, the dummy app controller and the experiment's ground truth.

## Run with Docker

Start the [dummy app](../dummy-app/README.md) first. From the `alert-detection-grouping` directory, run:

```powershell
docker compose up --build -d
docker compose logs -f --tail 20 alerts
```

Wait for `awaiting_first_sample` in the worker log to become empty before starting a fault experiment. This means F1 has received a fresh sample from each monitored service. Open `reports/index.md` on your host to find the incident and its summary.

The Compose project is called `observa-alerts`. It reaches `prometheus`, `loki` and `jaeger` by service name on the dummy app's external observability network. The default network is `observa-dummy_observability`. Set `OBSERVABILITY_NETWORK` if your dummy app uses a different network name. Only the observability network is required.

SQLite state lives in the `alert-state` volume. Reports are written directly to the bind-mounted `reports/` directory beside the Compose file. Git and Docker build rules exclude generated reports. On Linux, give container UID 10001 write access to this directory. On Windows, Docker Desktop handles file sharing.

When upgrading from the older Compose setup, keep the project name and the state volume. The first successful poll exports incidents from the existing state. The previous `alert-reports` volume can be retained. Avoid `docker compose down -v` when you want to preserve state.

## Run locally

Use Python 3.12 or later and a running dummy app. F1 uses the Python standard library. From this directory:

```powershell
python -m observa_alerts once --config config.json
python -m observa_alerts watch --config config.json
```

`once` takes one sample. Use `watch` to follow an incident through detection and recovery. Run one worker per data directory. SQLite preserves active incident IDs across restarts.

The default host ports are 9090 for Prometheus, 3100 for Loki and 16686 for Jaeger. Override their addresses with `PROMETHEUS_URL`, `LOKI_URL` and `JAEGER_URL`.

## Detection and recovery

F1 polls every 10 seconds. Most rules examine the preceding 60 seconds. A signal must remain bad for 20 seconds before its alert fires. A new incident starts as `open`, keeps its ID as its revision increases and becomes `resolved` once every attached alert has recovered. Each firing alert needs 60 seconds of consecutive healthy readings. Low traffic and missing data can leave a reading unknown. A sufficiently long polling gap resets the confirmation timers.

| Rule | Default trigger |
| --- | --- |
| HTTP errors | More than 15% HTTP 5xx responses, with at least 10 requests in the window |
| Latency | HTTP p95 above 2 seconds, with at least 10 requests |
| Database pool | More than 90% of connections in use, or an increase of more than 2 acquisition timeouts in the window |
| Payment failures | More than 25% of payments fail, with at least 10 payment requests. Failures include declines and service errors, such as 402 and 503. |
| Dependency errors | More than 15% of outbound calls return 5xx or fail in transport, with at least 10 calls |
| Unexpected exceptions | More than 3 distinct `unexpected_exception` events in the window |
| Persistence errors | More than 2 distinct `order_persistence_failed` events in the window |
| Telemetry gaps | Prometheus or Loki reports a collection problem, service uptime becomes stale or stops advancing, or services miss the initial coverage deadline |

Tune the thresholds for your workload in [config.json](config.json). F1 uses the changing uptime value to detect stale samples even when the collector republishes them with recent timestamps.

## How alerts are grouped

Specific signals, such as payment failures or database pool pressure, can anchor an incident. Compatible symptoms in the same service can join it with moderate confidence. To attach a generic alert from another service, F1 looks for a failed dependency call that names both services or a trace with failed spans in both.

For example, if Payments fails, Orders logs a failed call to Payments and the Gateway logs a failed call to Orders, F1 can group all three alerts. The Gateway's relationship points to Orders. Concurrent independent faults can form separate incidents. A generic alert that could belong to two existing groups joins one deterministically. Grouping records an observed relationship and leaves root-cause assessment to F3.

## Incident files and the F2 handoff

```text
reports/
├── index.md
├── index.json
└── INC-.../
    ├── incident.json
    ├── summary.md
    ├── evidence/
    │   ├── logs.jsonl
    │   ├── metrics.json
    │   ├── traces/
    │   └── .objects/
    └── snapshots/
```

Start with `incident.json`. It contains the incident `id`, `summary`, `first_observed_at`, `affected_services`, alerts, trigger values, thresholds, grouping reasons and evidence references. F1 adds `resolved_at` when recovery is confirmed. Each entry in the `alerts` dictionary identifies its parent incident. The `relationships` entries identify the linked alerts, their evidence and the confidence in each link. For the next stage, see [Run F2 on a saved incident](../investigation/README.md).

The compact bundle format uses `schema_version=2.0`, documented in `incident-bundle.schema.json`. The older `incident.schema.json` describes the v1 format used in archives, historical examples and internal state.

Use the incident's `snapshot_id` and the evidence `ref_id` to retrieve the exact evidence for a revision. F1 writes immutable evidence objects before publishing each snapshot and verifies SHA-256 hashes when reading them. The visible files `logs.jsonl`, `metrics.json` and `traces/index.json` show the latest state. Saved metrics include the observed values, queries and windows. Copy the entire incident directory to preserve the evidence and references on another machine.

Python callers can use `observa_alerts.evidence.read_evidence`. The evidence CLI also supports paginated reads. The defaults are 20 records and 16 KB per response, with caps of 100 records and 64 KB. Trace pages contain spans. The reader returns a `next_cursor` for more data and reports missing or corrupt evidence. [Evidence reader examples and file reference (Arabic)](INCIDENT_FILES_AR.md)

To migrate v1 reports, stop the worker and run `python -m observa_alerts.repack --reports reports` from this directory. Rebuild and restart the worker afterward. The tool verifies the evidence before moving old files into `reports/_legacy/`, preserves SQLite state and skips bundles it has already converted. Use one writer at a time. Older snapshots can still reference files in `.objects/`.

## Try a sample incident

```powershell
python -m observa_alerts demo --scenario payments
python -m observa_alerts demo --scenario pool
python -m observa_alerts demo --scenario deployment
python -m observa_alerts demo --scenario declines
```

Demo incidents carry `origin=synthetic_demo` and use separate output directories. The top-level examples include a historical v1 fixture. For the current format, open the [compact v2 example](examples/compact-bundle/demo-payments/index.md). For incidents captured during local Docker fault experiments, see the [live Docker examples](examples/live-docker/README.md).

## Operating limits

- Detection covers the rules above. CPU saturation, disk exhaustion and out-of-memory failures would need additional signals and rules.
- Each incident keeps up to 100 sanitized logs and 20 traces. A truncated log window prevents the absence of log events from confirming recovery.
- Grouping uses evidence visible at the time of the decision. Two incidents can stay separate if their connecting evidence arrives later.
- Thresholds are configured manually. The worker uses a single SQLite state store and publishes to the local filesystem. Plan for report and database retention, review thresholds and validate live data sources before operational use.
- Automatic seasonal baselines, external notifications, multi-user authentication and high-availability coordination are outside the current feature.

## Tests

```powershell
python -m unittest discover -s tests -v
```

See [validation results and coverage limits](VALIDATION.md) and [isolated Docker fault tests](tests/DOCKER_TESTS.md).
