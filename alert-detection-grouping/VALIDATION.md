# Validation

## Review and local report export — 2026-11-06

- 22 feature unit/HTTP tests passed, including exact downstream alert references,
  shared failed traces, local report/index updates, and retained resolved incidents
  after restarting the engine. Seven example incident JSON files passed the schema
  with date-time validation; this includes a clearly labeled synthetic example in
  `examples/local-reports/demo-payments/`.
- Compose configuration resolves `/app/reports` to the repository's `reports/`
  directory as a bind mount, retaining `observa-alerts_alert-state` for SQLite.
  Runtime incident files are ignored by Git and excluded from Docker image context.
- The dummy app's full unit suite passed: **807 passed, 2 skipped**, with one existing
  invalid-escape warning in a test string. Current app containers were observed
  healthy after Docker recovered. These checks do not establish exhaustive coverage.
- Fresh-volume Docker verification did **not** complete: project
  `observa-startup-check-4992aee9` applied database setup successfully, then Docker's
  API returned HTTP 500 during service startup. Automatic removal also failed.
  A later explicit stop succeeded for all containers in that test project; volumes
  and containers were retained. Results are in the dummy app's `test-results/`.
- A normal Docker Desktop restart initially timed out; the engine later responded,
  then failed again during the isolated run. The first new detector image build
  was interrupted with an EOF from BuildKit. No Docker reset or data deletion
  was performed.

### Final deployment and local-file verification

- The subsequent `docker compose up --build -d` succeeded: the new detector image
  built and its container was recreated and started using the existing state volume.
- The previously empty host `reports/` directory then contained **13 live incidents**,
  each as JSON and Markdown, plus `index.json` and `index.md`. This confirms actual
  container-to-host report export, not just a Compose configuration check.
- All 13 incident JSON files passed the schema and date-time validator. Every
  alert's `incident_id` matched its parent, both endpoints of every relationship
  referenced members of that incident, and index alert IDs/counts matched the files.
  Failed-dependency references agreed with the downstream service in their evidence.
- All 13 exported snapshots were `resolved`; their recorded source health was `ok`.
  Those are saved observations, not a current live-health guarantee.
- At the final inspection, Docker's engine pipe was unavailable again. The local
  files remain readable, but ongoing monitoring and fresh checkout/restart validation
  cannot be claimed while Docker is unavailable.

## Historical Docker validation — completed 2026-11-05

The following Docker validation passed for the earlier implementation. It does not
claim live validation of the November 6 report-mount and relationship-reference changes.

## Scope and isolation

- Built `observa-alerts:validation` from this feature and `observa-dummy:alert-validation` from the existing dummy source.
- Used generated, isolated Compose projects `observa-alert-validation-f89ce391` and `observa-alert-validation-f89ce391-detector` with separate database/state/report volumes and networks, and no published host ports.
- No dummy-app source/configuration was changed. The feature has its own folder, Dockerfile and Compose project.
- The detector ran as user `observa`, with a read-only root filesystem, all capabilities dropped and only the observability network. Its only mounted paths were `/app/data` and `/app/reports`.
- Only the evaluation probe accessed the fault controller/ground truth. The detector read real Prometheus, Loki and Jaeger APIs.
- The test used checkout traffic at 1 request/second and the shipped detector thresholds/timing. Initial startup at 4 requests/second caused real latency/errors on this host; after reducing test traffic and warming the stack, the baseline had healthy sources and no open incidents. Startup captures remain in the raw evidence.

## Live Docker results

| Case | Result | Incident creation delay | Grouping |
| --- | --- | --- | --- |
| Stable healthy baseline | Passed | — | No open incident at the two baseline observations |
| Payments returning 503 | Passed, recovered | 53.42 s | One incident: Payments, Orders, Gateway |
| Payments returning 402 | Passed, recovered | 56.18 s | Payment-failure alert on Payments only; no 5xx cascade asserted |
| Orders DB connection exhaustion | Passed, recovered | 40.60 s | One incident: Orders and Gateway |
| Bad deployment affecting order persistence | Passed, recovered | 36.58 s | One incident: Orders and Gateway |
| Prometheus stopped and restarted | Passed, recovered | Not benchmarked | Separate source-unavailable incident; missing metrics explicitly reported |
| Detector container restarted | Passed | — | All existing incident IDs preserved; no new duplicate incidents |

Delays are individual measurements from the fault activation response to incident creation, including collection/rule-window delays. They are not latency guarantees or percentile benchmarks. The expected primary alert fired at incident creation in each captured case.

The 503 test completed on November 4. The initial 402 attempt was interrupted overnight; its detection is retained in raw captures but excluded from the four completed scenario results. On November 5, a new stable baseline was confirmed and 402, pool, deployment, source outage and restart tests completed. The original successful 503 result was restored from its execution log and captures, then independently audited against its open and resolved JSON files.

The persisted-capture audit additionally verifies:

- Exactly one expected incident per injected business fault, with the expected affected services and rule.
- Cross-service relationships have actual failed-dependency log witnesses.
- Each business incident retains live logs, traces and trigger queries, with no reported source gaps during capture or confirmed recovery.
- Every alert is resolved in the recovered revision, while root cause remains explicitly `not_determined`.
- 402 produces only the payment-failure rule.
- Prometheus source failure and recovery, plus incident identity continuity across detector restart.

## Automated and contract checks

- All 18 Python unittest tests passed on the host and inside the built Docker image, including the HTTP adapter integration test. Those unit/HTTP fixtures are synthetic; the separate live tests above use real backends.
- Unit coverage includes healthy/noise guards, dependency propagation, unrelated services and distinct symptom families, missing data preventing false recovery, cached uptime, truncated Loki results, out-of-order samples, interrupted observation timers, recurrence, persistence and handoff export.
- Actual PromQL and LogQL queries ran successfully against Prometheus 3.1 and Loki 3.3; Jaeger error traces were fetched and retained.
- 227 saved incident snapshots passed `incident.schema.json` validation using Draft 2020-12 and date-time format checking.
- Docker image build, source-only Compose configuration and volume write permissions passed.
- Git inspection showed only the new `alert-detection-grouping/` folder; no tracked dummy-app differences.

## Evidence and reproduction

Start with [the readable incident guide](examples/live-docker/README.md).
The final 503 incident [JSON](examples/live-docker/resolved/INC-285cbc9ff73f595392e7.json) and [Markdown](examples/live-docker/resolved/INC-285cbc9ff73f595392e7.md) retain 6 alerts, 5 relationships, 100 sanitized logs and 20 traces. Both the open revision and the resolved revision are provided.
These are real local-test telemetry captures (`origin=live`), not production incidents. The separate original demo example remains `origin=synthetic_demo`.

Curated machine-readable evidence lives in `test-results/docker-2026-11-05/`:

- `results.json`: runner checkpoints and case outcomes.
- `capture-audit.json`: stronger assertions against persisted incidents and recoveries.
- `runtime-checks.json`: container user, mounts and network isolation.
- `schema-check.json`: saved schema-validation result.

Full local captures, evaluation-only fault records, generated manifests and execution logs remain in the ignored directory `validation/observa-alert-validation-f89ce391/`.
To repeat the tests, follow [tests/DOCKER_TESTS.md](tests/DOCKER_TESTS.md).
After validation, both isolated test projects were stopped and removed. Their volumes and all exported reports were retained; no test containers remain running.

## Limits of these results

- This is an initial rules-based implementation, not exhaustive error coverage or production readiness. CPU, disk, OOM, security and other uninstrumented failure modes need additional signals and rules.
- There is no large-scale false-positive/false-negative study, long-running load test or production threshold calibration.
- Simultaneous independent faults and missing data during an active business incident were tested with fixtures, not injected simultaneously in the live Docker run. The live Prometheus outage occurred after business incidents recovered.
- The live run did not stop Loki, Jaeger or an individual business container. Source/transport failure and stale telemetry handling also have automated fixture coverage.
- Evidence-based grouping is not proven causality. Incidents already opened separately are not later merged when new evidence appears.
- Evidence is bounded, and Jaeger originals may expire. The retained JSON samples remain usable after the isolated test containers stop.
- The worker is single-instance. Downstream delivery is JSON/Markdown files with `id`/`revision`, not an implemented queue/API or an end-to-end test of the future analysis service.
