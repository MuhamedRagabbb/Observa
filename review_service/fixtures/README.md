# F1–F4 mock fixtures for F6/F7

These files let F6 and F7 be built and tested before F1–F5 are complete. Each one follows `OBSERVA_SHARED_CONTRACTS_v1.md` (proposed v1, with the additive F2 fields in `investigation/CONTRACT_MIGRATION_v1.1.md`). Each is validated by `review_service/tests/test_fixtures_contract.py` and by `build_fixtures.py` before it is written. No file contains secrets, live credentials or fault-injection ground truth.

| Folder | Model | Files |
|---|---|---|
| `f1/` | `observa_reports.inputs.F1Output`: contract `Incident` plus its `Alert` records | `incident_payments_open`, `incident_payments_resolved`, `incident_db_exhaustion`, `incident_bad_deployment` |
| `f2/` | `observa_investigation.contracts.InvestigationResult` | `investigation_payments`, `investigation_partial`, `investigation_db_exhaustion`, `investigation_bad_deployment` |
| `f3/` | `observa_reports.inputs.CorrelationResultIn` (F3 public contract output) | `correlation_payments_completed`, `correlation_inconclusive`, `correlation_contradiction`, `correlation_failed`, `correlation_db_exhaustion`, `correlation_bad_deployment` |
| `f4/` | `observa_reports.inputs.RecommendationResultIn` (F4 is not built yet; shape from the contract) | `recommendations_payments`, `recommendations_empty`, `recommendations_db_exhaustion`, `recommendations_bad_deployment` |
| `bundles/` | `observa_reports.inputs.ReportInputs` (what F7 assembles a version from) | `payments_v1`, `payments_v2_revised`, `db_exhaustion`, `bad_deployment`, `inconclusive`, `missing_f4`, `failed_f3` |

## Provenance

- **Payments (real data).** Derived from the local `payments_errors` run of incident `INC-6558d5b7036a44934e9e` (2026-11-23):
  - F1 `incident.json` (revision r21), mapped to the contract shape (`id`→`incident_id`, `summary`→`title`, alert IDs as a list, `first_observed_at`→`start_time`, `resolved_at`→`end_time`, severity `warning`→`medium`). The open variant has `end_time: null`.
  - F2 `f2/investigation.initial.json`, as published by the worker.
  - F3 `f3/output.json`, regenerated from `f3/.state/correlation.json` with the corrected contract adapter (commit `3120972`: cited snapshot only, related rows as `context_only`).
  - `correlation_failed` is F3's own `failed_contract_result(...)` for a Groq HTTP 429.
- **Derived payments variants:**
  - `correlation_inconclusive`: one tentative candidate, a null leader and a rejected follow-up.
  - `correlation_contradiction`: adds a `contradicts` link and an unresolved (`reference_valid: "unknown"`) link.
  - `recommendations_*`: F4 has no implementation yet, so these follow the contract.
  - `payments_v2_revised`: a new F2 run (`parent_run_id` = the v1 run), a new F3 run and recommendations for it.
- **DB exhaustion and bad deployment (synthetic).** Deterministic IDs and realistic claims in the F2 and F3 output formats.
- **`investigation_partial`.** A copy of `investigation/fixtures/investigation_result_partial.json` (metrics failed).

## Regenerate

The payments inputs come from a local run directory (ignored by git), so regenerating needs those files:

```sh
python review_service/fixtures/build_fixtures.py \
  --f1-incident alert-detection-grouping/reports/INC-6558d5b7036a44934e9e/incident.json \
  --f2-investigation correlation_agent/runs/INC-6558d5b7036a44934e9e/f2/investigation.initial.json \
  --f3-correlation <F3 public output.json regenerated with the corrected adapter> \
  --f3-failed <failed_contract_result JSON for INC-6558d5b7036a44934e9e>
```

The output is deterministic: identical inputs produce byte-identical files.
