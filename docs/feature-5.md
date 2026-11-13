# Feature 5: Incident Knowledge Memory

## Status

Memory stores incidents whose report a human has approved. Producing one needs findings
(Feature 2), a root cause (Feature 3), recommendations (Feature 4), a report (Feature 7)
and an approval (Feature 6). None of those exist yet, so there is no real approved
incident to store. Code will be published here once real data is available.

The user stories are drafts pending instructor review. Everything below that depends on
another feature is a proposal, not an agreed contract.

## Goal

Keep the reviewed findings of past incidents, and give the relevant ones back to later
investigations and to engineers, so the team does not investigate the same problem from
zero each time.

## Where it sits

```
report (F7) -> human review (F6) -- Approve ---------> Incident Knowledge Memory
                                 -- Request Changes -> feedback loop (never reaches memory)

Incident Knowledge Memory -> correlation agent (F3), through its check_memory tool,
                             filling relevant_data_from_memory in the correlation state
                          -> recommendation agent (F4)
                          -> chatbot agent (optional)
```

Feature 5 stores and retrieves. It does not diagnose. Deciding the current root cause and
the evidence still missing belongs to Feature 3.

The architecture diagram labels the two review outcomes "agree" and "disagree". The
Feature 6 stories call them **Approve** and **Request Changes**; this document uses those.

## What it should do

1. **Save on approval.** When the reviewer approves a report, save that incident's
   knowledge, linked to its source incident and the exact report version approved.
   Rejected or pending reports never enter memory. Processing the same approval twice
   must not create a second entry.
2. **Keep the full picture.** Each entry holds the incident context, approved findings,
   the final root cause, recommendations, and references to the supporting logs, metrics
   and traces. Anything unknown is recorded as unknown, never filled in.
3. **Retrieve relevant history.** Given the current incident and its findings, return the
   relevant approved entries, each with the reason it matched (shared services, symptoms
   or findings) and its source incident.
4. **Keep history separate from the present.** A retrieved entry is labelled historical.
   A similar past incident is context, and never by itself confirms the current root cause.
5. **Allow inspection.** An engineer can open a stored incident and read its findings,
   root cause, recommendations and evidence references. Evidence that is no longer
   available is shown as unavailable. Reading changes nothing.
6. **Follow approved revisions.** When a corrected report is approved, it becomes the
   current version. Earlier approved versions stay traceable.
7. **Fail honestly.** "Nothing relevant found" is a different outcome from "memory could
   not be read". Neither may produce invented history. A failed save is reported and can
   be retried without creating duplicates.

## What each feature provides

| From | Feature 5 needs | Their stories | Code today |
|---|---|---|---|
| F1 Alert detection & grouping | Incident ID, title, alerts, affected services, start and end time, evidence references | #6, #7, #8 | Yes, on the F1 branch |
| F2 Multi-signal analysis | Findings and their evidence references | #21 to #24 | No |
| F3 Correlation | Final root cause with confidence; calls `check_memory` | #9, #12, #14 | No |
| F4 Recommendation | Recommendations; reads similar past incidents | #10, #11, #13 | No |
| F6 HITL & feedback | The review decision for an exact report version | #15, #16, #17 | No |
| F7 Report | The report, with a stable report ID and version number | #18, #19, #20 | No |

What those stories already say about Feature 5:

- **F6-US-01 (#15):** an approved report is handed off to Feature 5, and Feature 6 does
  not build the memory store. A report with requested changes is never sent. Each decision
  is tied to the correct incident and report version, and a duplicate action must not
  cause a duplicate handoff. Its review contract lists incident ID, report and version
  reference, reviewer decision, optional feedback and review status.
- **F6-US-03 (#17):** only an explicitly approved report version may proceed to Feature 5.
- **F7-US-03 (#20):** every report has a stable report ID, an incident ID and a version
  number. A revision is a new version, never an overwrite. Feature 7 does not approve or
  write to memory; Feature 6 controls the handoff.
- **F7-US-01 (#18):** a missing input is reported as unavailable or inconclusive, and an
  unverified cause is not presented as proven. Memory keeps the same distinction.
- **F4-US-01 (#10):** the recommendation agent uses similar past incidents from memory
  when available. Each option has a description, expected impact and related root cause.
- **F3 (#9, #12, #14):** none of the three correlation stories mentions memory or
  `check_memory`, although the architecture diagram gives that tool to the correlation
  agent and IKM-03 depends on it. This needs to be agreed with the Feature 3 owner.

### Feature 1 hand-off, as it stands on its branch

Feature 1 writes an `incident.json` per incident (contract version 2.0). It covers the
incident context part of a memory entry:

| Memory entry | Feature 1 field | Note |
|---|---|---|
| Incident ID | `id` | Stable across updates; Feature 1 also keeps a `revision` and `snapshot_id` |
| Title | `summary` | There is no field named `title` |
| Alerts | `alerts` | An object keyed by alert ID, with rule, service, severity and values |
| Start and end time | `first_observed_at`, `resolved_at` | No `resolved_at` while the incident is open |
| Affected services | `affected_services` | |
| Evidence references | `evidence_refs` with `snapshot_id` | Read through Feature 1's evidence reader |

Feature 1 leaves the root cause as `not_determined`, by design.

F1-US-03 (#8) names the incident fields `incident_id`, `title`, `alerts`, `start_time`,
`end_time` and `affected_services`, as the architecture diagram does. The code on the
Feature 1 branch uses `id`, `summary`, `first_observed_at` and `resolved_at` instead.
Which names are final should be confirmed with the Feature 1 owner before any code
depends on them.

## Proposed memory entry

- **Source:** incident ID, approved report ID and version, approval decision ID, approval time
- **Incident context:** title, alerts, affected services, start and end time
- **Findings:** the approved findings
- **Root cause:** what the approved report concluded, kept apart from any cause that was
  later independently verified
- **Recommendations:** each with its action and expected impact. Being approved, being
  executed, and having resolved the incident are recorded as three separate facts
- **Evidence references:** signal type (logs, metrics or traces), service, and whether
  the evidence is still available
- **Missing information:** stated explicitly
- **Reviewer comment:** when the reviewer left one

## Proposed operations

| Operation | Used by | Purpose |
|---|---|---|
| Save approved incident | F6, on Approve | Store one approved report version |
| `check_memory` | F3, F4 | Relevant history for the current incident and findings |
| Search | Engineers | Find past incidents by text and service |
| Get entry, list versions | Engineers | Read-only inspection and version history |

Read results distinguish three outcomes: matches found, no match, and retrieval failed.

## User stories

| ID | Story | Issue |
|---|---|---|
| IKM-01 | Save approved incident knowledge | #5 |
| IKM-02 | Preserve incident context and supporting evidence | #25 |
| IKM-03 | Retrieve relevant historical incidents | #26 |
| IKM-04 | Assess whether historical knowledge applies | none yet |
| IKM-05 | Inspect a retrieved incident | none yet |
| IKM-06 | Ask questions about past incidents (chatbot, optional) | none yet |
| IKM-07 | Keep knowledge current after an approved revision | none yet |
| IKM-08 | Handle missing or unavailable memory | none yet |

Acceptance criteria and technical tasks for IKM-01 to IKM-03 are in their issues.

## Open decisions

- Is approval alone enough to store an incident, or must the incident also be closed?
- Is the chatbot (IKM-06) part of the first release?
- How is relevance decided and ranked?
- Who may read stored knowledge, and how long is it kept?
- Does the correlation agent call `check_memory`? The Feature 3 stories do not say so yet.
- Which incident field names are final: those in F1-US-03 or those on the Feature 1 branch?
- The exact shapes of Feature 2 findings, the Feature 3 root cause, Feature 4
  recommendations, the Feature 6 decision and the Feature 7 report.

## Data rule

Feature 5 must never read the dummy app's controller, its ground-truth journal, or the
controller and traffic logs, and the dummy app's fault scenario names must not appear in
stored knowledge or test data. Those exist only to score OBSERVA offline.
