# Validation record

> **Historical record of the first implementation run (2026-10-24).** It predates the React storefront and operator, the multi-item checkout and the P2-P5 work; mentions below of the "Arabic storefront" describe that run. Current status is in the README's "Project status"; a final validation is planned for P6.

Verified at 2026-10-24T16:48:59.248026+00:00 against the local Docker Desktop Linux engine.

- **40 Python tests passed**, zero failures/errors/skips, in 63.297 seconds: 31 unit/contract tests and nine live integration/scenario tests.
- **Five JavaScript client-contract tests passed** using Node's built-in test runner.
- The Arabic storefront at http://localhost:8000/ and separate operator interface at http://localhost:8090/ returned HTTP 200 from the final containers.
- Grafana, Prometheus, Loki and Jaeger query/readiness endpoints returned HTTP 200.
- No automatic schedule was running when this report was saved. The active scenario at the checked timestamp was `none`. The tests themselves completed their cleanup; current controller state is recorded separately in `test-results/gui-evidence.json`.

## Checks run

```powershell
docker compose --profile ui up --build -d --wait --wait-timeout 180
.venv/Scripts/ruff.exe check services controllers scripts tests
.venv/Scripts/ruff.exe format --check services controllers scripts tests
.venv/Scripts/python.exe -m compileall -q services controllers tests
$env:RUN_LIVE_TESTS='1'
$env:TEST_COLLECTOR_OUTAGE='1'
.venv/Scripts/python.exe -m pytest -q --tb=short --junitxml=test-results/gui-acceptance.xml
node --test tests/ui/client.test.mjs
```

All frontend JavaScript modules passed Node's module syntax check.

## Browser verification

The running interfaces were exercised with the in-app browser against the actual container APIs. A notebook checkout completed and appeared in browser history; another tab read the saved history. Activating 100% payment failure through the operator form caused checkout to display a translated payment-unavailable result. Deactivation restored the operator baseline state. The interface accepted a buggy-release activation, completed a short automatic rotation, and stopped another automatic schedule during warmup. Forms displayed live incident state/countdown and disabled conflicting actions.

The layout was visually inspected in the available browser panel (approximately 600 pixels wide), with no horizontal DOM overflow. A standalone mobile device and wider desktop viewport were not used. The operator screenshot is `test-results/ui-control.jpg`. Browser history is per origin/browser, capped at 100 records, and is not a global order list.

## Application and observability evidence

The live suite used real PostgreSQL databases and all four application services. It verified checkout/status retrieval, credential ownership isolation, concurrent inventory/payment idempotency, and rejection of a late reservation after release. A checkout trace was queried from Jaeger across all four services and DB spans; correlated JSON logs were queried from Loki; request metrics existed for each business service with bounded attributes.

F1 reached at least 95% of configured Orders pool capacity and produced acquisition timeouts/upstream errors before recovery. F2 produced payment/checkout errors with compensation and no artificial delay. F3 changed release metadata and produced a slow persistence span/new exception signature, then recovered on rollback. Live TTL expiry and automatic rotation passed. The Collector outage test stopped/restarted Collector and confirmed checkout still succeeded.

UI/static/config requests are excluded from application telemetry. Shopping API calls retain instrumentation. Fault-control calls stay on the controller origin; no control proxy was added to Gateway. Controller/traffic resources and ground-truth labels were absent from application telemetry queries.

## Runtime findings and limits

The previous in-memory Jaeger configuration approached its 256 MiB container limit and trace delivery stalled during verification. Retention now defaults to 2,000 traces (`JAEGER_MAX_TRACES`) with a 192 MiB Go soft memory budget and one Go scheduler worker. Jaeger was restarted, resetting its volatile trace history; database, metric and log volumes were preserved. Log acceptance queries now use the relevant incident/request time window instead of scanning the retained hour of historical traffic. The final complete acceptance run passed after these adjustments.

F3 is a runtime release simulation; F1 exhausts only the Orders client pool. Jaeger remains bounded and volatile: configure retention and container memory together for longer investigations. Telemetry queues can drop records during prolonged outages while JSON stdout remains available. Compensation is best-effort, with reconciliation states for ambiguous persistence failures. No OBSERVA agent or production deployment was added.

## Local artifacts

- `test-results/gui-acceptance.xml`: final Python acceptance results.
- `test-results/gui-evidence.json`: timestamped checked state.
- `tests/ui/client.test.mjs`: checkout/network/correlation/escaping client tests.
- `ground-truth/incidents.jsonl`: isolated evaluation records, never an agent input.
- `README.md`: both interfaces, configuration and API runbook.

The Compose stack is left running. Use the operator interface to inspect or stop the current schedule.
