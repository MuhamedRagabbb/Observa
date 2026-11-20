"""Build the F3 agent and run one F2 investigation through it."""
import json

from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware, before_agent, before_model, wrap_model_call
from langchain_core.messages import HumanMessage
from openai import APIError

from investigation.observa_investigation.contracts import SIGNALS
from .model import MODEL, SYSTEM_PROMPT, groq_model
from .state import CorrelationResult, CorrelationState
from .tools import (MemoryLookup, check_memory, collect_supported_observations, get_structured_file,inspect_finding, request_evidence, submit_cause)


@wrap_model_call
def require_one_tool(request, handler):
    return handler(request.override(tool_choice="required", model_settings={
        **request.model_settings, "parallel_tool_calls": False,
    }))


@before_model(can_jump_to=["end"])
def finish_after_valid_decision(state, runtime):
    if state.get("result") is not None:
        return {"jump_to": "end"}


def build_graph(memory_lookup: MemoryLookup | None = None, *, model=None):
    @before_agent
    def prepare(state, runtime):
        report = get_structured_file(state["investigation"])
        reports = [report, *[get_structured_file(r) for r in state.get("additional_investigations", [])]]
        if any(r.incident_id != report.incident_id for r in reports):
            raise ValueError("Investigations must belong to one incident.")
        rows = [row for r in reports for row in collect_supported_observations(r)]
        history = state.get("history")
        if history:
            rows.extend(history.release_findings)
        memory = check_memory(report, memory_lookup)
        # The model cites row numbers. Keep the full hashes and evidence links in
        # state for validation and publication instead of repeating them in the prompt.
        snapshots = {sid: index for index, sid in enumerate(dict.fromkeys(
            row.snapshot_id for row in rows), 1)}
        context = {
            "incident_id": report.incident_id, "status": report.status,
            "observations": [
                {"row": i, "snapshot": snapshots[row.snapshot_id], "finding_id": row.finding_id,
                 "signal": row.signal, "service": row.service, "claim": row.claim}
                for i, row in enumerate(rows, 1)
            ],
            "incident_history": {
                "opening_snapshot": snapshots.get(history.first_snapshot),
                "phase": history.phase, "first_observed_at": history.first_observed_at,
                "resolved_at": history.resolved_at, "affected_services": history.affected_services,
                "peaks": [p.model_dump(exclude={"evidence_id"}) for p in history.peaks],
                "dependencies": [d.model_dump(include={"caller", "callee", "signal"})
                                 for d in history.dependencies],
            } if history else None,
            "investigations": [], "history": memory,
        }
        for r in reports:
            findings = [f for s in SIGNALS for f in getattr(r, f"{s}_output").findings]
            context["investigations"].append({
                "run_id": r.run_id, "signals": {s: getattr(r, f"{s}_output").status for s in SIGNALS},
                "missing_evidence": [g.model_dump() for g in r.missing_evidence],
                "inferences": [f.model_dump(include={"finding_id", "service", "claim", "signal_type"})
                               for f in findings if f.claim_kind == "inference"],
                "limitations": list(dict.fromkeys(f.uncertainty for f in findings if f.uncertainty)),
            })
        return {"investigation": report, "observations": rows, "memory": memory,
                "messages": [HumanMessage(content=json.dumps(context, ensure_ascii=False, separators=(",", ":")))]}

    return create_agent(
        model=model if model is not None else groq_model(),
        tools=[inspect_finding, submit_cause, request_evidence],
        system_prompt=SYSTEM_PROMPT,
        state_schema=CorrelationState,
        middleware=[prepare, finish_after_valid_decision, ModelCallLimitMiddleware(run_limit=5),
                    require_one_tool],
        name="correlation_agent",
    )


def correlate(investigation, memory_lookup: MemoryLookup | None = None, *,
              history=None, additional_investigations=()) -> CorrelationResult:
    try:
        inputs = {"investigation": investigation, "additional_investigations": list(additional_investigations)}
        if history is not None:
            inputs["history"] = history
        state = build_graph(memory_lookup).invoke(inputs)
    except APIError as exc:
        status = getattr(exc, "status_code", None)
        raise RuntimeError(f"Groq {MODEL}: {type(exc).__name__}" +
                           (f" (HTTP {status})" if status else "")) from None
    if "result" not in state:
        raise RuntimeError("Groq did not produce a valid correlation decision within five turns.")
    return state["result"]
