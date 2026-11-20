# F1 incidents captured in Docker

These incidents were recorded during fault experiments in the local checkout application on November 4–5, 2026. The detector used Prometheus, Loki and Jaeger. The files have `origin: live` because the telemetry came from running services. They are historical v1 exports. For the current bundle layout, see the [compact example](../compact-bundle/demo-payments/index.md).

## Payment failure

Incident `INC-285cbc9ff73f595392e7` contains six alerts across three services. Orders logged failed calls to Payments, and the Gateway logged failed calls to Orders. These records explain why F1 grouped the alerts. Compare the [open snapshot](during/INC-285cbc9ff73f595392e7.json), [resolved snapshot](resolved/INC-285cbc9ff73f595392e7.json) and [incident summary](resolved/INC-285cbc9ff73f595392e7.md).

| Signal | Service | Peak | Alert threshold |
| --- | --- | ---: | ---: |
| Payment failures, including declines | Payments | 100% | >25% |
| HTTP 5xx | Payments | 50% | >15% |
| HTTP 5xx | Orders | 72.09% | >15% |
| HTTP 5xx | Gateway | 54.39% | >15% |
| Dependency failures | Orders | 19.62% | >15% |
| Dependency failures | Gateway | 57.41% | >15% |

Each peak is the highest reading in a measurement window. The peaks can occur at different times.

| Event on November 4 | UTC |
| --- | --- |
| First abnormal reading | 18:47:43 |
| Incident opened | 18:48:13 |
| Last abnormal reading | 18:49:43 |
| Recovery confirmed | 18:51:03 |

Revision 18 records the recovery. Use `trigger_evidence` and `peak_value` to inspect the failure after the latest values have returned to normal. The export contains 100 log entries, 20 traces and five relationships. In F1, `root_cause.status` remains `not_determined`. Follow the recorded calls and their span parents to investigate the origin. Source URLs in the JSON refer to the original Docker network.

## Other captures

| Experiment | What F1 grouped | Resolved incident |
| --- | --- | --- |
| Payment declined (402) | A single Payments alert | [JSON](resolved/INC-c31f55e7783d687cab4d.json) |
| Database pool pressure | Orders and Gateway alerts | [JSON](resolved/INC-96c88fde5c0f9737dfd3.json) |
| Deployment regression in order persistence | Orders and Gateway alerts | [JSON](resolved/INC-ec18b33a2376586dcea5.json) |

Each JSON export has a Markdown summary alongside it. [Validation notes](../../VALIDATION.md)
