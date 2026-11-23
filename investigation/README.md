# F2 · Multi-signal investigation

F2 investigates one F1 incident and returns findings for logs, metrics and traces. Each signal includes its status and evidence references. Missing evidence is recorded explicitly. The Docker worker passes this result to F3 and can run a targeted follow-up when F3 asks a question.

## Run automatically

Follow the [repository quickstart](../README.md) to start the application and F1. Put `GROQ_API_KEY` in both local files, `investigation/.env` for F2 and `correlation_agent/.env` for F3. From the **repository root**, start the shared worker and follow its output.

```sh
docker compose -f correlation_agent/compose.yaml up --build -d
docker compose -f correlation_agent/compose.yaml logs -f --tail 20 investigate
```

After a report is published, open `correlation_agent/runs/<incident-id>/investigation.json`. The fields `logs_output`, `metrics_output` and `traces_output` are the three agents' results. For a final report with a distinct peak-impact snapshot, the worker also writes `investigation.impact.json`. While F3 is pending, the worker keeps completed F2 analyses in the incident’s `.f2-cache/` directory. Targeted answers appear under the relevant signal's `answers`. [Example investigation](fixtures/investigation_result_completed.json)

## Run F2 on a saved incident

Use the same two local configuration files and build the worker image once. Run these PowerShell commands from the repository root. They select the most recently updated incident from the F1 index. If the index is empty, run a fault experiment first.

```powershell
docker compose -f correlation_agent/compose.yaml build investigate
$incident = Get-Content alert-detection-grouping/reports/index.json -Raw | ConvertFrom-Json | Sort-Object updated_at -Descending | Select-Object -First 1 -ExpandProperty id
docker compose -f correlation_agent/compose.yaml run --rm --no-deps --entrypoint python investigate -m observa_investigation --reports /reports --incident $incident --no-llm
```

The command prints one F2 result as JSON and skips model calls. To run a single signal agent, replace `-m observa_investigation` with `-m logs_agent`, `-m metrics_agent` or `-m traces_agent` and add `--agent`. For the normal Groq-backed workflow and saved Markdown reports, use the automatic worker above. [F1 evidence](../alert-detection-grouping/README.md) · [F3 report](../correlation_agent/README.md)
