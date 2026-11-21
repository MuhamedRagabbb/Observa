# F3 · Incident correlation

F3 uses findings from the logs, metrics and traces investigations to identify the probable origin of an incident and explain how it affected other services. Each step in the report cites its evidence. If the evidence is insufficient, the report identifies what is missing. [Example report](examples/example_report.md)

## Run with Docker

Start the checkout app and F1 using the [project quickstart](../README.md). F2 and F3 share a worker. Set `GROQ_API_KEY` in `investigation/.env` for F2 and in `correlation_agent/.env` for F3. From the repository root:

```sh
docker compose -f correlation_agent/compose.yaml up --build -d
docker compose -f correlation_agent/compose.yaml logs -f --tail 20 investigate
```

The worker checks for incidents every two seconds while idle. It queues one opening assessment per incident and a final assessment when F1 confirms recovery. If it first sees an incident after recovery, it can generate the final report directly from the saved snapshots.

Open `correlation_agent/runs/<incident-id>/` on the host:

| File | Contents |
| --- | --- |
| `report.initial.md` | Opening assessment, when one was published |
| `report.md` | Latest published assessment, including the evidence for each step |
| `report.json` | The same report as structured data. Check `history.phase` for `initial` or `final`. |
| `investigation.json` | F2 findings for the opening snapshot used in the assessment |
| `investigation.impact.json` | Additional F2 findings when the final assessment uses a different peak-impact snapshot |
| `correlation.json` | Full F3 result, including the report and any follow-up request |

To process one incident manually, replace `INC-REPLACE-ME` with an ID from `alert-detection-grouping/reports/index.md`. The command writes to a separate, timestamped directory under `correlation_agent/runs/`.

```sh
docker compose -f correlation_agent/compose.yaml run --rm --no-deps investigate INC-REPLACE-ME
```

Stop the worker with `docker compose -f correlation_agent/compose.yaml stop`. For an independent investigation, see [Run F2 on a saved incident](../investigation/README.md#run-f2-on-a-saved-incident).

## How to read the result

F2 analyses the opening snapshot and, if different, the snapshot with the most services showing bad readings. F3 combines those findings with F1's alert peaks, failed calls, release events and recovery timestamps. The steps describe a proposed chain of effects. Use the incident history for measured times.

A probable cause requires supporting evidence from two distinct signal types for the same service and snapshot. When F3 requests more evidence, the Docker worker sends its questions to the relevant F2 agents. The report includes their answers and citations. The worker stops on a repeated request or after two follow-up rounds by default. If Groq is unavailable, the worker retries and reuses completed analyses from the incident's `.f2-cache/` directory.

When saved rows do not answer a follow-up question, the Docker worker can query Loki and Prometheus for the affected service in the original snapshot window. Database pool evidence includes acquisition failures and time-aligned active and maximum connection counts. Citations ending in `#live-<hash>` resolve to `follow-up-evidence/live-<hash>.json` beside the report. Each file contains the source query, time window and observations. Expired source data can leave a question unanswered. The worker uses the dummy app's observability network (`OBSERVABILITY_NETWORK` overrides its name) and the Groq keys configured for this installation.

## Run from Python

From the repository root, install the dependencies and use a saved F2 result as input. Set `GROQ_API_KEY` in the environment or in `correlation_agent/.env`. F3 sends the findings and any supplied historical notes to Groq's `openai/gpt-oss-120b` model.

```sh
python -m pip install -r correlation_agent/requirements.txt
python -m correlation_agent investigation/fixtures/investigation_result_completed.json --report-out correlation-report.json
python -m correlation_agent --from-report correlation-report.json --report-out correlation-report.md
```

The first invocation prints the full result and saves its report as JSON. The second renders that saved report as Markdown offline. In Python, import `correlate` from `correlation_agent` and pass an F2 result or its dictionary. In standalone mode the caller handles follow-up requests and can supply historical notes through the optional `memory_lookup` callback.
