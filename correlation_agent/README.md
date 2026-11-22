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

```text
<incident-id>/
├── f2/
│   ├── investigation.initial.json
│   ├── investigation.impact.json
│   ├── investigation.json
│   ├── follow-up-evidence/
│   └── .cache/
└── f3/
    ├── output.initial.json
    ├── output.initial.md
    ├── output.json
    ├── output.md
    └── .state/
```

`f2/` contains specialist findings and cached follow-up evidence. `investigation.initial.json` is F2's opening analysis, `investigation.impact.json` holds a separate peak-impact analysis when needed, and `investigation.json` holds the latest unmarked analysis. `f3/output.json` is the latest public F3 result in the proposed shared contract v1 shape. `f3/output.md` is its readable incident report. The `.initial` pair preserves the opening assessment, while the unmarked pair tracks the latest result and becomes final after F1 resolves the incident. `.state/` contains internal checkpoints used to resume retries and follow-ups.

To process one incident manually, replace `INC-REPLACE-ME` with an ID from `alert-detection-grouping/reports/index.md`. The command writes to a separate, timestamped directory under `correlation_agent/runs/`.

```sh
docker compose -f correlation_agent/compose.yaml run --rm --no-deps investigate INC-REPLACE-ME
```

Stop the worker with `docker compose -f correlation_agent/compose.yaml stop`. For an independent investigation, see [Run F2 on a saved incident](../investigation/README.md#run-f2-on-a-saved-incident).

## How to read the result

F2 analyses the opening snapshot and, if different, the snapshot with the most services showing bad readings. F3 combines those findings with F1's alert peaks, failed calls, release events and recovery timestamps. The steps describe a proposed chain of effects. Use the incident history for measured times.

A probable cause requires supporting evidence from two distinct signal types for the same service and snapshot. When F3 requests more evidence, the Docker worker sends its questions to the relevant F2 agents. The report includes their answers and citations. The worker stops on a repeated request or after two follow-up rounds by default. If Groq is unavailable, the worker retries and reuses completed analyses from the incident's `f2/.cache/` directory.

The current Docker worker runs the bounded F3-to-F2 follow-up loop as an interim integration: F3 decides when to submit a follow-up and passes the cycle count to F2. F2 validates and executes each request. The long-term design is for F2 to own follow-up scheduling and limits through its investigation service.

The F2 handoff additions are recorded as a proposed migration in [CONTRACT_MIGRATION_v1.1.md](../investigation/CONTRACT_MIGRATION_v1.1.md). It remains a draft until the contract owners approve it.

When saved rows do not answer a follow-up question, the Docker worker can query Loki and Prometheus for the affected service in the original snapshot window. Database pool evidence includes acquisition failures and time-aligned active and maximum connection counts. Citations ending in `#live-<hash>` resolve to `follow-up-evidence/live-<hash>.json` under `f2/follow-up-evidence/`. Each file contains the source query, time window and observations. Expired source data can leave a question unanswered. The worker uses the dummy app's observability network (`OBSERVABILITY_NETWORK` overrides its name) and the Groq keys configured for this installation.

## Run from Python

From the repository root, install the dependencies and use a saved F2 result as input. Set `GROQ_API_KEY` in the environment or in `correlation_agent/.env`. F3 sends the findings and any supplied historical notes to Groq's `openai/gpt-oss-120b` model.

```sh
python -m pip install -r correlation_agent/requirements.txt
python -m correlation_agent investigation/fixtures/investigation_result_completed.json --report-out correlation-report.json
python -m correlation_agent --from-report correlation-report.json --report-out correlation-report.md
```

The first invocation prints the full result and saves its report as JSON. The second renders that saved report as Markdown offline. In Python, import `correlate` from `correlation_agent` and pass an F2 result or its dictionary. In standalone mode the caller handles follow-up requests and can supply historical notes through the optional `memory_lookup` callback.

## Run F3 tests locally

From the repository root, install the package dependencies and make the local signal-agent packages importable before running the F3 tests:

```powershell
python -m pip install -r correlation_agent/requirements.txt
$env:PYTHONPATH = ".;alert-detection-grouping;us01_logs_agent;us02_metrics_agent;us03_traces_agent"
python -m unittest discover -s correlation_agent/tests -v
```
