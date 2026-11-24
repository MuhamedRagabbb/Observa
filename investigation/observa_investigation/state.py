from typing import Annotated, Dict, List, Optional, TypedDict


def _merge_agents(
    left: Optional[Dict[str, "AgentState"]],
    right: Optional[Dict[str, "AgentState"]],
) -> Dict[str, "AgentState"]:
    """Merge per-signal agent updates from parallel Send nodes."""
    merged = dict(left or {})
    if right:
        merged.update(right)
    return merged


class AgentState(TypedDict):
    status: str                  # pending | running | completed | partial | inconclusive | failed
    attempts: int
    answers: List[dict]
    findings: List[dict]         # SignalFinding dumps, replaced (never appended) on every attempt
    missing_evidence: List[dict]
    error: Optional[str]


class InvestigationState(TypedDict, total=False):
    incident: dict
    run_id: str
    plan_id: str
    follow_up_targets: List[str]
    parent_run_id: Optional[str]
    plan: Dict[str, List[str]]   # signal -> objectives
    agents: Annotated[Dict[str, AgentState], _merge_agents]
    result: dict
