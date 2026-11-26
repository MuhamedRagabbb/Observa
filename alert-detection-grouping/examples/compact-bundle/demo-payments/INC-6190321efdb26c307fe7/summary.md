# INC-6190321efdb26c307fe7

**Status:** open | **Severity:** critical
**Origin:** synthetic_demo | **Environment:** local
**Revision:** 4

Observed payment symptoms in payments-service

First observed (UTC): 2026-11-04T10:00:00Z
Updated (UTC): 2026-11-04T10:00:50Z
Resolved (UTC): not yet confirmed

Affected services: payments-service, orders-service, api-gateway

All 4 alerts below belong to incident **INC-6190321efdb26c307fe7**.

| Alert ID | Rule | Service | Status | Latest value | Threshold | Unit | Quality |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 74a55410973bf5a95b33 | http_errors | api-gateway | firing | 0.7 | 0.15 | ratio | bad |
| 145cbaa27f2d469d3c89 | http_errors | orders-service | firing | 0.7 | 0.15 | ratio | bad |
| 983c3dbbb004b61eae17 | http_errors | payments-service | firing | 0.7 | 0.15 | ratio | bad |
| 45bc9975ca15de626cfc | payment_failures | payments-service | firing | 0.85 | 0.25 | ratio | bad |

## Why these alerts are grouped

- orders-service (145cbaa27f2d469d3c89) linked to payments-service (45bc9975ca15de626cfc): failed_dependency; confidence: **high**. Upstream service logged a failed call to a service already in this incident. Causation remains a hypothesis.
  Call direction: orders-service → payments-service.
- payments-service (983c3dbbb004b61eae17) linked to payments-service (45bc9975ca15de626cfc): same_service_symptoms; confidence: **moderate**. Overlapping observations in one service; shared cause is unconfirmed.
- api-gateway (74a55410973bf5a95b33) linked to orders-service (145cbaa27f2d469d3c89): failed_dependency; confidence: **high**. Upstream service logged a failed call to a service already in this incident. Causation remains a hypothesis.
  Call direction: api-gateway → orders-service.

## Evidence and handoff

Send [incident.json](incident.json) to the agent first. Full evidence is stored separately.
Pinned input: [this snapshot](snapshots/r4-695ea705dc19c1c72d7c4a53ef2a5c47126d535741b337ec22e4917611a4afa7/incident.json).
Evidence map: [manifest](snapshots/r4-695ea705dc19c1c72d7c4a53ef2a5c47126d535741b337ec22e4917611a4afa7/manifest.json).

| Evidence ref | Saved records |
| --- | --- |
| logs | 8 |
| metrics | 8 |
| traces | 1 |
| relationships | 3 |
| timeline | 5 |
| details | 5 |

Metrics are observed alert values/queries, not complete time series. Logs and traces are bounded samples.
**Root cause: not determined.** Grouping is not proof of causation.

## Data gaps

No source gaps reported in this saved snapshot.

## Next investigation steps

- Check failed dependency directions against span parent relationships.
- Compare metrics with a healthy baseline and check release_changed events.
- Distinguish business payment declines from service unavailability.
- Validate suspected initiating service; grouping is not proof of causality.
