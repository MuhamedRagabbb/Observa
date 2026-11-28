# F4 · Evidence-Backed Remediation Recommendations

F4 consumes a typed F3 `CorrelationResult`, optional F5 `MemorySearchResult` (approved historical knowledge only), and an evidence resolver to produce a validated and deterministically ranked `RecommendationResult` for **F7 Report**.

F4 **never** assembles final incident reports (`create_final_detailed_report` belongs to F7), **never** approves or executes fixes, and **never** fabricates remediations when F3 is `inconclusive`.

## Pipeline stages

1. **`generate_recommendation_options` (F4-US-01)**
- Uses LangChain `ChatOpenAI` with Groq (`openai/gpt-oss-120b` via `GROQ_API_KEY` / `LLM_API_KEY`) when configured, or deterministic rule-based generation (`use_llm=False` / `--no-llm`).
   - Generates `remediation`, `verification`, and `investigation` options with stable `REC-...` IDs, expected impact, risks/caveats, and candidate links.
   - When F3 status is `inconclusive` or `leading_candidate_id` is `null`, blocks remediation generation and emits only grounded `verification`/`investigation` options (or `[]` if no gaps/observations warrant one).
   - Consumes F5 `search_approved_incidents` safely (`ok + []` vs `failed`), treating historical incidents as secondary context only.

2. **`validate_recommendation_evidence` (F4-US-02)**
   - Purely deterministic validation (LLM outputs cannot bypass checks).
   - Verifies reference integrity, availability (`available` vs `unavailable` / `expired`), time-window overlap, service relevance, and support vs contradiction.
   - Missing, irrelevant, expired, or contradicting citations yield `unsupported` or `conditional` with explicit reasons instead of automatic validation.

3. **`rank_recommendation` (F4-US-03)**
   - Excludes `unsupported` options from `ranked_recommendation_ids`.
   - Orders validated options deterministically using documented weights over validation tier (`supported` > `conditional`), action type, evidence strength/signal diversity, urgency, risk, and uncertainty.
   - Outputs `ranked_recommendation_ids` and nullable `top_recommendation_id` inside `RecommendationResult` (`F4 -> F7`).

## Run from Python

```powershell
$env:PYTHONPATH = ".;alert-detection-grouping;us01_logs_agent;us02_metrics_agent;us03_traces_agent;investigation"
python -m recommendation_agent correlation_agent/runs/<incident-id>/f3/output.json --no-llm
```

With F3 internal state and F1 evidence resolution:

```powershell
python -m recommendation_agent correlation_agent/runs/<incident-id>/f3/output.json `
  --f3-state correlation_agent/runs/<incident-id>/f3/.state/correlation.json `
  --reports-dir alert-detection-grouping/reports `
  --out recommendation.json
```

## Run tests

```powershell
$env:PYTHONPATH = ".;alert-detection-grouping;us01_logs_agent;us02_metrics_agent;us03_traces_agent;investigation"
python -m unittest discover -s recommendation_agent/tests -v
```

## Docker pipeline

The root [`compose.yaml`](../compose.yaml) runs the `recommend` watcher after `investigate` starts. It waits for each F3 output, validates against F1 retained evidence, reads approved F5 memory in read-only mode, then writes `f4/output.json`, `f4/output.md`, and `f4/state.json` alongside the corresponding F3 run. Initial and final results are separately checkpointed. The watcher retries a failed processing step on its next scan; successful input revisions are not processed again. Set `F4_USE_LLM=0` for local deterministic runs.
