"""Build the F3 agent and run one F2 investigation through it."""
import json

from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware, before_agent, before_model, wrap_model_call
from langchain_core.messages import HumanMessage
from openai import APIError

from investigation.observa_investigation.contracts import SIGNALS
from .model import MODEL, SYSTEM_PROMPT, groq_model
from .state import CorrelationResult, CorrelationState
from .tools import (MemoryLookup, check_memory, collect_supported_observations, get_structured_file,
                    inspect_finding, request_evidence, submit_cause)


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
        rows = collect_supported_observations(report)
        memory = check_memory(report, memory_lookup)
        context = {
            "incident_id": report.incident_id, "status": report.status,
            "observations": [dict(row=i, **r.model_dump()) for i, r in enumerate(rows, 1)],
            "signal_status": {s: getattr(report, f"{s}_output").status for s in SIGNALS},
            "source_findings": [
                {"finding_id": f.finding_id, "kind": f.claim_kind, "claim": f.claim,
                 "service": f.service, "signal": s, "uncertainty": f.uncertainty}
                for s in SIGNALS for f in getattr(report, f"{s}_output").findings
            ],
            "missing_evidence": [g.model_dump() for g in report.missing_evidence], "history": memory,
        }
        return {"investigation": report, "observations": rows, "memory": memory,
                "messages": [HumanMessage(content=json.dumps(context, ensure_ascii=False))]}

    return create_agent(
        model=model if model is not None else groq_model(),
        tools=[inspect_finding, submit_cause, request_evidence],
        system_prompt=SYSTEM_PROMPT,
        state_schema=CorrelationState,
        middleware=[prepare, finish_after_valid_decision, ModelCallLimitMiddleware(run_limit=5),
                    require_one_tool],
        name="correlation_agent",
    )


def correlate(investigation, memory_lookup: MemoryLookup | None = None) -> CorrelationResult:
    try:
        state = build_graph(memory_lookup).invoke({"investigation": investigation})
    except APIError as exc:
        status = getattr(exc, "status_code", None)
        raise RuntimeError(f"Groq {MODEL}: {type(exc).__name__}" +
                           (f" (HTTP {status})" if status else "")) from None
    if "result" not in state:
        raise RuntimeError("Groq did not produce a valid correlation decision within five turns.")
    return state["result"]
