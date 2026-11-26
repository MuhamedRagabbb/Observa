# Feature 5: Incident Knowledge Memory

## Status

Memory stores incidents whose report a human has approved, and gives relevant past
incidents back to later investigations and to engineers.

As of 2026-11-26:

- F1, F2 and F3 are on `main`. F5 reads their real output formats; see the integration guide.
- F6 and F7 have code on their shared branch `F6_HTL-Feedback` (no pull request yet). F5
  implements their `MemorySink` port and was run against their review service locally.
- F4 has no code. F5 offers a read connection point shaped after what F7 expects from F4.

The F5 code is on the `F5_Incident_knowledge_memory` branch: `observa/f5_memory/`, with
tests in `tests/` and fake data in `fixtures/`. What each team needs to do is in
"Requests to other features" below.

Every shape owned by another feature is that team's **proposal** until it is merged into
`main` and agreed. Most follow `OBSERVA_SHARED_CONTRACTS_v1`, which is itself a draft. The
user stories are drafts pending instructor review.

## Goal

Keep the reviewed findings of past incidents, and give the relevant ones back to later
investigations and to engineers, so the team does not investigate the same problem from
zero each time.

## Where it sits

```
report (F7) -> human review (F6) -- Approve ---------> Incident Knowledge Memory (F5)
                                 -- Request Changes -> feedback loop (never reaches memory)

Incident Knowledge Memory -> correlation agent (F3), filling relevant_data_from_memory
                          -> recommendation agent (F4)
                          -> chatbot agent (optional, not built)
```

Feature 5 stores and retrieves. It does not diagnose. Deciding the current root cause and
the evidence still missing belongs to Feature 3.

## What it does

1. **Saves on approval only.** A save takes an F6 decision ID. Memory re-reads that
   decision from F6 and the exact report version from F7, checks the report's SHA-256
   digest, and stores nothing unless the decision approves that exact version. Request
   Changes never enters memory. The same approval delivered twice is stored once.
2. **Keeps the full picture.** Each entry is the approved F7 report version, unchanged:
   incident context, structured findings per signal, the leading root-cause candidate and
   alternatives, recommendations, the evidence register, missing evidence and limitations.
   Unknown values stay null or `unknown`.
3. **Retrieves relevant history** for the current incident context and findings, with the
   reason each entry matched (shared services, terms and the fields they matched in).
4. **Keeps history separate from the present.** Results are labelled `historical`, carry a
   caveat, and list what the past and current incidents share and where they differ
   (services, indicators, signals). A past cause is never written into the current incident.
5. **Allows read-only inspection** of a stored version, its version history and the
   evidence that is no longer available (`unavailable_evidence_ids`).
6. **Follows approved revisions.** The latest approved version is used for search; older
   approved versions stay readable. A late, older approval never replaces a newer one.
7. **Fails honestly.** Reads return `{"status": "ok" | "failed", "items": [...], "error"}`:
   no match is `ok` with no items. A failed save raises a typed error and can be retried
   without creating a duplicate.

## Integration guide

All code is Python 3.11+ standard library, in `observa/f5_memory/`. Import from the
repository root (`from observa.f5_memory ... import ...`). Every read result is the envelope
above; every item has `knowledge_origin: "historical"` and a `relevance_basis` with a
`caveat`. `ranking_score` only orders results; it is not a probability.

### F1: incident context

```python
from observa.f5_memory.f1_adapter import incident_context_from_f1, alert_findings_from_f1

context = incident_context_from_f1(incident_json)   # F1 incident.json, format 1.0 or 2.0
alert_terms = alert_findings_from_f1(incident_json)   # one {"claim", "indicator", "service"} per alert
```

`context` is `{incident_id, title, alerts, start_time, end_time, affected_services}`, mapped
from F1's `id`, `summary`, `alerts` (IDs), `first_observed_at`, `resolved_at`.
`end_time` is null while the incident is open; a missing `summary` is an error, never an
invented title. `observability:*` entries in `affected_services` are kept but never count
as a shared service. F1's own root cause and hypotheses are not used.

F1 needs to change nothing to be read. Tested read-only against the five real incident
files (four incidents, one captured both open and resolved) in
`alert-detection-grouping/examples/live-docker/`. F1's titles share fixed wording
("Observed ... symptoms in ..."), so memory ignores that wording; the alert rule names in
`alert_terms` are what make a match possible before F2 has run.

### F2: investigation findings

```python
from observa.f5_memory.f2_adapter import check_memory_for_investigation, findings_from_investigation

result = check_memory_for_investigation(memory, context, investigation_result)
```

`investigation_result` is F2's `InvestigationResult` (JSON or the pydantic model). Memory
searches on each finding's `claim` and `indicator`; it stores nothing from the current
incident. F2's newer `IncidentV1` (on its branch) uses the same field names as `context`
(not tested yet). Tested against F2's example results,
`investigation/fixtures/investigation_result_*.json`.

### F3: correlation

F3's code on `main` already has a memory hook:
`MemoryLookup = Callable[[str, list[str]], list[str]]`, called as
`lookup(incident_id, services)`; the strings go into the LLM prompt as `history`. F5 fits it
without any change to F3's code:

```python
from observa.f5_memory import IncidentMemory
from observa.f5_memory.f1_adapter import incident_context_from_f1, alert_findings_from_f1
from observa.f5_memory.f3_adapter import f3_memory_lookup

memory = IncidentMemory("data/incident-memory-v3.sqlite3")   # no approval source: reads only
lookup = f3_memory_lookup(memory, {
    "incident": incident_context_from_f1(incident_json),
    "findings": alert_findings_from_f1(incident_json),
    "investigation": investigations[0],          # F2 result the worker already has
})
result = correlate(investigations[0].model_dump(), memory_lookup=lookup, history=history, ...)
```

In F3's Docker worker this is the `correlate(...)` call in
`correlation_agent/docker/run.py` (`correlate_with_follow_up`), which today passes no
`memory_lookup`, so memory is always empty in the running pipeline.

Limits of this adapter, all caused by the current signature:

- F3 passes no title or findings, so the adapter needs the context above. Without it, it
  returns `[]`.
- "No match" and "memory failed" both return `[]`. The full result of the last call is on
  `lookup.last_result` (`status`, `items`, `error`), but F3 does not read it.
- F3 gets one labelled line per match, not structured IDs, reasons or the comparison.
- The adapter never raises; a memory error becomes `[]` so correlation continues on current
  evidence.

To remove these limits, F3 would pass the `InvestigationResult` (or at least the finding
claims) to the lookup and accept the envelope, so `relevant_data_from_memory` can record
`ok` or `failed` and keep the source IDs. Until that is agreed, the adapter is the bridge.
Memory text sent this way reaches F3's LLM provider (Groq).

### F4: recommendations (F4 has no code yet)

```python
from observa.f5_memory.integration import similar_incidents_for_correlation_result

result = similar_incidents_for_correlation_result(memory, context, f3_output, investigation=None)
```

`f3_output` is F3's public `ContractCorrelationResult` (`f3/output.json`); the leading
candidate's `description` is the query. Each item carries the past report's
`accepted_recommendations` and `other_recommendation_options` in F7's shape (`action`,
`action_type`, `validation`, `rationale`, `risk_or_caveat`, `evidence_links`), plus
`execution_status` and `resolution_status`, which are `unknown` because no feature records
them yet. They are history for F4 to weigh, not recommendations for the current incident.

### F6 and F7: saving approved reports (their branch, proposal)

F6 delivers `memory.approved_report.v1` to a `MemorySink.store(handoff)` port, at least
once. F5 implements it:

```python
from observa.f5_memory.f6_handoff import HttpReviewServiceClient, IncidentMemorySink

sink = IncidentMemorySink.connect("data/incident-memory-v3.sqlite3",
                                  HttpReviewServiceClient("http://127.0.0.1:8095", viewer_token))
relay = build_relay(settings, engine, memory_sink=sink)   # review_service/observa_review_service/worker.py
```

`store` returns `None` when the version is stored, or was already stored with the same
content, and raises `MemoryOperationError` otherwise, so F6's outbox retries it and shows
`failed` after its last attempt. Before saving, memory reads `GET /api/v1/reports/{id}/review`
(the decision) and `GET /api/v1/reports/{id}/versions/{v}` (the content) with a `viewer`
token, and checks the content against `content_digest`.

Without changing F6's worker, the same check runs on the files F6's stand-in writes:

```bash
OBSERVA_REVIEW_TOKEN=<viewer token> python3 -m observa.f5_memory --db data/incident-memory-v3.sqlite3 \
  import-handoffs <F6 MEMORY_SINK_DIR> --review-url http://127.0.0.1:8095
```

Tested on 2026-11-26 against F6/F7 at `370df90`, run locally from a separate copy: two demo
versions were approved through the review API and stored, once through `import-handoffs`
and once through F6's own relay with `IncidentMemorySink`. F6 then showed
`memory_sync_status: stored` for both, and importing again created no duplicate. That data
was a scratch database and was not kept.

## Stored entry (format 3)

| Part | Content |
|---|---|
| Source | incident ID, report ID and version, content reference and digest, decision ID, reviewer ID, approval time |
| Incident context | title, alert IDs, affected services, start and end time, severity (as F7 gives it) |
| Findings | per signal: finding ID, service, claim, observation or inference, indicator, evidence links |
| Root cause | `final_root_cause` (the report's leading candidate) and `alternative_root_causes`, each with its uncertainty; no numeric confidence. `verified_root_cause` stays null: no feature verifies a cause |
| Recommendations | ranked options and other options from the report; execution and resolution `unknown` |
| Evidence | F7's evidence register (`valid`, `invalid`, `unresolved`) and `unavailable_evidence_ids` |
| Gaps | missing evidence and limitations as the report states them |
| Reviewer comment | null on approval: F6 keeps no comment on Approve |

## User stories

| ID | Story | Issue | State |
|---|---|---|---|
| IKM-01 | Save approved incident knowledge | #5 | Done; F6 sink tested against the real review service |
| IKM-02 | Preserve incident context and supporting evidence | #25 | Done for what F7 reports; no time window per evidence item upstream |
| IKM-03 | Retrieve relevant historical incidents | #26 | Done in F5; F3 uses it only through the adapter until F3 changes its hook |
| IKM-04 | Assess whether historical knowledge applies | none yet | Done in F5 (labels, caveat, comparison); recording needed evidence is F3's |
| IKM-05 | Inspect a retrieved incident | none yet | Done (`get`, `history`, unavailable evidence) |
| IKM-06 | Ask questions about past incidents (chatbot, optional) | none yet | Not built: waiting for a team decision |
| IKM-07 | Keep knowledge current after an approved revision | none yet | Done; tested with two approved versions from F6 |
| IKM-08 | Handle missing or unavailable memory | none yet | Done in F5; lost through F3's `list[str]` hook (see F3 limits) |

## Commands

```bash
python3 -m unittest discover -s tests -v
python3 -m observa.f5_memory --db /tmp/f5-demo.sqlite3 seed-demo
python3 -m observa.f5_memory --db /tmp/f5-demo.sqlite3 search "connection pool timeout" --service orders-service
python3 -m observa.f5_memory --db /tmp/f5-demo.sqlite3 history INC-201
```

`seed-demo` loads `fixtures/approval_snapshots.json`: fake, synthetic approvals in F7's
report format (8 approvals, 2 change requests). The default database is
`data/incident-memory-v3.sqlite3`; a file in an older format is reported and left as is.

Error codes: `VALIDATION_ERROR`, `NOT_APPROVED`, `NOT_AUTHORIZED`, `NOT_FOUND`,
`NOT_ELIGIBLE`, `VERSION_CONFLICT`, `SCHEMA_MISMATCH`, and the retryable `STORAGE_FAILURE`
and `SOURCE_UNAVAILABLE` (the review service could not be read).

`IncidentMemory(..., require_ended_incident=True)` stores only reports of incidents with an
end time (`NOT_ELIGIBLE` otherwise). It is off by default, so approval alone is enough until
the team decides.

Search limits: keyword overlap only, no synonyms or negation, weights checked only against
fake data and F1's few real examples, and every search scans all records.

## Requests to other features

| Feature | Request | Why F5 needs it |
|---|---|---|
| F1 | Make `summary` required in `incident-bundle.schema.json` | F5 rejects an incident without a title rather than inventing one |
| F2 | Agree who maps F1's field names (`id`, `summary`, `first_observed_at`, `resolved_at`) to the contract names your branch now requires | F5 maps them for itself; the rest of the pipeline needs the same mapping |
| F3 | Pass `memory_lookup=f3_memory_lookup(...)` in `correlate(...)` in `docker/run.py` | Without it, memory is always empty in the running pipeline |
| F3 | Pass findings to the lookup and accept `{"status", "items", "error"}` instead of `list[str]` | Through `list[str]`, "no match" and "memory failed" look the same (IKM-08), and source IDs are lost |
| F3 | Confirm that memory text may be sent to Groq | Stored reports go into the LLM prompt |
| F4 | Confirm that F7's recommendation shape is the one F4 will produce | F5 stores and returns exactly that shape |
| F6/F7 | Confirm `ApprovedReportHandoff` and `ReportContent` are final, and open a PR to `main` | F5's storage format follows them |
| F6/F7 | Give F5 a `viewer` token for the review service | Memory re-reads each decision and report version before saving |
| F6 | Decide whether a report of an open incident may be approved | Affects which reports reach memory |
| F7 | Leave the draft notice out of approved versions, and fix the executive summary ending in `..` when an action ends with a full stop | Every stored report keeps the "not stored as approved incident knowledge" text, because it is part of the hashed content |

## Open decisions

- Is approval alone enough to store an incident, or must the incident also be closed? F6
  allows approving a report of an open incident.
- Is the chatbot (IKM-06) part of the first release?
- How is relevance decided and ranked?
- Who may read stored knowledge, and how long is it kept? F3 sends memory text to an
  external LLM provider.
- F3's memory hook shape (see the F3 section).
- Whether F6's handoff and F7's report shapes are final, and F4's recommendation shape.

## Data rule

Feature 5 never reads the dummy app's controller, its ground-truth journal, or the
controller and traffic logs, and the dummy app's fault scenario names do not appear in
stored knowledge or test data. Those exist only to score OBSERVA offline.
