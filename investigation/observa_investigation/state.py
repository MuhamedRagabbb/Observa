from typing import Dict, List, Optional, TypedDict


class AgentState(TypedDict):
    status: str                  # pending | completed | partial | inconclusive | failed
    attempts: int
    findings: List[dict]         # SignalFinding dumps, replaced (never appended) on every attempt
    missing_evidence: List[dict]
    error: Optional[str]


class InvestigationState(TypedDict, total=False):
    incident: dict
    run_id: str
    plan_id: str
    parent_run_id: Optional[str]
    plan: Dict[str, List[str]]   # signal -> objectives
    agents: Dict[str, AgentState]
    result: dict
