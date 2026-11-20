# F3 correlation agent

## Docker with an existing F1 reports folder

F2 and F3 run in one worker. Put `GROQ_API_KEY` in both local files,
`investigation/.env` for F2 and `correlation_agent/.env` for F3.
Run these commands from the repository root.

```powershell
docker compose -f correlation_agent/compose.yaml up --build -d
docker compose -f correlation_agent/compose.yaml logs -f investigate
```

The worker polls F1 every two seconds while idle. An open incident gets an opening
report in `runs/<incident-id>/report.initial.md`. A resolved snapshot gets a final
`report.md`, including when the worker first discovers an already resolved incident.
The original filenames expose the latest report; the opening copy remains available.
The SQLite queue deduplicates opening jobs by incident and final jobs by snapshot.
Upgrades retain the previous watcher's explicitly excluded historical baseline.

The Docker runner pins F2 to the opening snapshot and, if different, the snapshot
with the most simultaneously affected services (breaking ties by active bad alerts).
The three F2 agents use Groq (openai/gpt-oss-120b) to interpret non-empty signal profiles.
If a
model response is unavailable or rejected, F2 records the fallback in the findings.
Their findings are saved in `investigation.json` and `investigation.impact.json`.
F3 reads immutable F1 snapshots up to the selected revision for per-alert peaks,
recovery time, directed failed calls and release/error timing. Raw log and trace
record citations use zero-based cursors for F1's evidence reader. A release match
requires the same service and version and, when supplied, deployment ID. The
reported first error is the earliest matching error in the retained sample.
`report.json` and `report.md` carry the same history and citations.
A request for evidence triggers targeted F2 follow-up. The same report includes the
questions, answers and citations. The worker stops on a repeated request or after
the configured limit (two rounds by default). It caches snapshot analyses and
completed rounds in the incident's `.f2-cache/` so retries resume after the last successful step.
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

For the standalone F3 Python CLI, set `GROQ_API_KEY` in the process environment.
The CLI also accepts `correlation_agent/.env` as a local fallback.
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
A partial report contains observations and gaps. In standalone Python mode the caller handles the follow-up.

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
