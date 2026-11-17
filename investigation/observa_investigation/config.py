from dataclasses import dataclass


@dataclass(frozen=True)
class InvestigationConfig:
    agent_timeout_s: float = 180.0       # one deadline per dispatch round, shared by parallel agents
    max_retries: int = 1                 # extra attempts after the first one, only for agents that FAILED
    max_reinvestigations: int = 2        # separate bound for F3 follow-up loops
    max_review_cycles: int = 3           # separate bound for F6 human review cycles
