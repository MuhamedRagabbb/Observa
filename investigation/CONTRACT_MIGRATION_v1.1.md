# Shared contract migration draft: v1.1

**Status:** Draft for owner review. This document records implementation additions; it does not imply team approval of the shared contract.

| Addition | R/O/N | Owner | Meaning |
|---|---|---|---|
| `SignalFinding.indicator` | O, nullable | F2 | Machine-readable metric rule, log event, or span operation behind a finding. `null` when the source has no stable indicator. |
| `EvidenceAnswer` | R for each answer | F2 | A targeted follow-up question, its answer, snapshot, signal, and evidence IDs. |
| `SignalOutput.answers` | R; empty list allowed | F2 | Answers produced by follow-up analysis. It is always serialized; `[]` means no follow-up answers were produced. |
| `FollowUpRequest.signal_objectives` | O, non-null when present | F2/F3 | Per-signal objectives that scope each specialist's follow-up. An omitted or empty map falls back to `objectives`. |
| `AgentTask.follow_up` | Internal execution flag; not serialized | F2 | Tells a signal agent it is answering a targeted follow-up rather than running the initial investigation. |

## Compatibility notes

- Existing readers should accept `indicator` as absent or `null` and `answers` as absent in older saved results. New writers always include `answers`, including an empty list.
- `signal_objectives` and `follow_up` are additive and do not change the meaning of existing `objectives` or initial investigations.
- F2 owns the signal finding and answer fields. F2 and F3 jointly own the follow-up objective map.
- F2 owner sign-off and a reviewed update to `OBSERVA_SHARED_CONTRACTS_v1.md` are still required before treating this draft as an approved v1.1 contract.
