# F3 correlation agent

## Docker with an existing F1 reports folder

In PowerShell, from the Observa repository root:

```powershell
docker compose -f correlation_agent/compose.yaml up --build -d
docker compose -f correlation_agent/compose.yaml logs -f investigate
```

The worker polls the F1 reports mount every two seconds while idle. On its first
scan it skips existing incidents. Each newly published incident ID runs F2 on
local evidence and then F3 on Groq, using `correlation_agent/.env`. Later updates
to the same ID do not trigger another run. The worker persists its queue in
`correlation_agent/runs/.watch.sqlite3`, catches up after restarts, and retries
failed jobs with backoff. Each incident's outputs are in `runs/<incident-id>/`:
`investigation.json`, `correlation.json`, `report.json`, and `report.md`.
A valid F3 request for more evidence is a completed handoff for this worker.
Stop the worker with `docker compose -f correlation_agent/compose.yaml stop`.
For a manual one-off run, pass an explicit ID to `run --rm investigate INC-...`.

## Local Python

From the Observa repository root, install the F3 dependencies and run an F2 fixture:

```powershell
python -m pip install -r correlation_agent/requirements.txt
python -m correlation_agent investigation/fixtures/investigation_result_completed.json
python -m correlation_agent investigation/fixtures/investigation_result_completed.json --report-out correlation-report.json
python -m correlation_agent --from-report correlation-report.json --report-out correlation-report.md
```

Set `GROQ_API_KEY` in `correlation_agent/.env` or the process environment.
The agent calls Groq's OpenAI-compatible endpoint using `openai/gpt-oss-120b`.
Each investigation sends the F2 findings, missing evidence and any supplied
historical notes to Groq. The CLI prints the F3 result as JSON.

Every result includes a structured `report`; the optional `--report-out` saves only
that object as JSON for the recommendation stage, or as Markdown for reading.
Choose the format using the filename extension. Use `--from-report` to convert an
existing standalone report offline. The report contains the incident/run IDs, status,
a readable summary, numbered steps, evidence and any remaining gaps. Step 1 in a
completed report is the probable origin. Each inferred effect points to its parent
with `follows_step` and cites the F2 claims, signal types and evidence IDs for the
affected service. Step order describes a proposed causal chain, not measured timestamps.
A partial report contains independent observations and gaps; the caller routes the
accompanying `follow_up_request` to F2.

* `agent/model.py` configures the Groq model and system instructions.
* `agent/graph.py` builds the agent with LangChain's `create_agent` and limits it to five model calls.
* `agent/tools.py` filters evidence, inspects references and validates the chosen handoff.
* `agent/state.py` defines the graph state and the public result.

* `reporting/report.py` builds the report and renders Markdown.
* `examples/` contains a sample JSON report and its Markdown rendering.

The model can inspect an F2 finding, submit ranked candidate causes, or request
specific evidence from the F2 agents. Python derives evidence links from cited
observation rows and checks that a final cause has two distinct signals on the
same service and snapshot. These checks establish citation validity; the causal
interpretation is the model's judgment. A follow-up request is returned for the
caller to dispatch. Groq failures surface as errors.

Python callers can use `from correlation_agent import correlate` and pass an F2
`InvestigationResult` or its dictionary. The optional `memory_lookup(incident_id,
services)` callback supplies historical notes.
