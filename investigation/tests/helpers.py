import time

from observa_investigation.agents.base import AgentResult
from observa_investigation.contracts import EvidenceLink, SignalFinding

INCIDENT = {"incident_id": "INC-1", "snapshot_id": "r1-abc",
            "affected_services": ["orders-service"], "start_time": "2026-11-16T22:52:28Z"}


def finding(signal, n=1, signal_type=None):
    return SignalFinding(
        finding_id=f"INC-1:{signal}:F{n}", signal_type=signal_type or signal, service="orders-service",
        claim=f"{signal} observation {n}", claim_kind="observation",
        evidence_links=[EvidenceLink(evidence_id=f"ev-{signal}-{n}", relation="supports", reference_valid=True,
                                     relevance_status="relevant", support_status="supported")],
        execution_status="completed", uncertainty=None)


def ok(signal, n=1):
    return AgentResult(status="completed", findings=[finding(signal, n)])


class ScriptedAgent:
    """Plays its steps in order (the last one repeats). An Exception is raised; (seconds, result) sleeps first."""

    def __init__(self, signal, *steps):
        self.signal, self.steps, self.tasks = signal, steps, []

    @property
    def calls(self):
        return len(self.tasks)

    def run(self, task):
        step = self.steps[min(len(self.tasks), len(self.steps) - 1)]
        self.tasks.append(task)
        if isinstance(step, tuple):
            time.sleep(step[0])
            step = step[1]
        if isinstance(step, Exception):
            raise step
        return step
