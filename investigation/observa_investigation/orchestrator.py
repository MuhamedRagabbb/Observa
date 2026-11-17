"""Investigation Orchestrator (F2-US-04): plan -> dispatch -> (retry) -> aggregate,
with LangGraph checkpointer persistence, FollowUpRequest (F3), and FeedbackIntent (F6)."""
import uuid
from concurrent.futures import ThreadPoolExecutor, wait
from typing import Any, Dict, List, Optional, Set

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from .agents.base import AgentResult, AgentTask, SignalAgent
from .config import InvestigationConfig
from .contracts import (
    SIGNALS,
    FeedbackIntent,
    FollowUpRequest,
    InvestigationResult,
    MissingEvidence,
    SignalFinding,
    SignalOutput,
)
from .state import InvestigationState


def incident_id_of(incident: dict) -> Optional[str]:
    return incident.get("id") or incident.get("incident_id")


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


class Orchestrator:
    def __init__(
        self,
        agents: Dict[str, SignalAgent],
        config: Optional[InvestigationConfig] = None,
        checkpointer: Optional[Any] = None,
    ):
        absent = [s for s in SIGNALS if s not in agents]
        if absent:
            raise ValueError(f"no agent registered for: {absent}")
        self.agents = agents
        self.config = config or InvestigationConfig()
        self.checkpointer = checkpointer if checkpointer is not None else MemorySaver()
        self.graph = self._build()

    # ---- public: initial investigation --------------------------------
    def investigate(
        self,
        incident: dict,
        *,
        run_id: Optional[str] = None,
        plan_id: Optional[str] = None,
        parent_run_id: Optional[str] = None,
    ) -> InvestigationResult:
        if not incident_id_of(incident):
            raise ValueError("incident has no id")
        actual_run_id = run_id or _new_id("RUN")
        cfg = {"configurable": {"thread_id": actual_run_id}}
        final = self.graph.invoke(
            {
                "incident": incident,
                "run_id": actual_run_id,
                "plan_id": plan_id or _new_id("PLAN"),
                "parent_run_id": parent_run_id,
            },
            config=cfg,
        )
        return InvestigationResult.model_validate(final["result"])

    # ---- public: F3 FollowUpRequest ------------------------------------
    def follow_up(
        self,
        request: FollowUpRequest,
        incident: dict,
        prior_result: Optional[InvestigationResult] = None,
        *,
        reinvestigation_count: int = 0,
    ) -> InvestigationResult:
        iid = incident_id_of(incident)
        if not iid or iid != request.incident_id:
            request.status = "rejected"
            raise ValueError(f"FollowUpRequest incident_id {request.incident_id!r} does not match incident {iid!r}")

        if reinvestigation_count >= self.config.max_reinvestigations:
            request.status = "rejected"
            raise ValueError(
                f"RETRY_LIMIT_REACHED: re-investigation count ({reinvestigation_count}) "
                f"reached limit ({self.config.max_reinvestigations})"
            )

        targets: Set[str] = set(request.target_agents) if request.target_agents else set(SIGNALS)
        invalid = targets - set(SIGNALS)
        if invalid:
            request.status = "rejected"
            raise ValueError(f"invalid target_agents in FollowUpRequest: {sorted(invalid)}")

        request.status = "accepted"

        agents_init: Dict[str, dict] = {}
        for s in SIGNALS:
            if s in targets:
                agents_init[s] = {
                    "status": "pending",
                    "attempts": 0,
                    "findings": [],
                    "missing_evidence": [],
                    "error": None,
                }
            elif prior_result:
                out: SignalOutput = getattr(prior_result, f"{s}_output")
                agents_init[s] = {
                    "status": out.status,
                    "attempts": 1,
                    "findings": [f.model_dump() for f in out.findings],
                    "missing_evidence": [m.model_dump() for m in prior_result.missing_evidence if m.requested_from == s],
                    "error": None,
                }
            else:
                agents_init[s] = {
                    "status": "pending",
                    "attempts": 0,
                    "findings": [],
                    "missing_evidence": [],
                    "error": None,
                }

        inc = incident
        start = inc.get("first_observed_at") or inc.get("start_time") or "unknown start"
        end = inc.get("last_observed_at") or inc.get("end_time") or "ongoing"
        services = ", ".join(inc.get("affected_services", [])) or "all services"
        window = f"for {services} from {start} to {end}"

        plan = {
            "logs": [f"Find errors, exceptions and abnormal events in logs {window}"],
            "metrics": [f"Find deviations in latency, error rate and resource use {window}"],
            "traces": [f"Find slow or failed spans and the services they affect {window}"],
        }
        for s in targets:
            if request.objectives:
                plan[s] = list(request.objectives)

        run_id = _new_id("RUN")
        plan_id = _new_id("PLAN")
        cfg = {"configurable": {"thread_id": run_id}}

        final = self.graph.invoke(
            {
                "incident": incident,
                "run_id": run_id,
                "plan_id": plan_id,
                "parent_run_id": request.source_run_id,
                "plan": plan,
                "agents": agents_init,
            },
            config=cfg,
        )
        request.status = "completed"
        return InvestigationResult.model_validate(final["result"])

    # ---- public: F6 FeedbackIntent -------------------------------------
    def handle_feedback(
        self,
        intent: FeedbackIntent,
        incident: dict,
        prior_result: Optional[InvestigationResult] = None,
        *,
        review_cycle_count: int = 0,
    ) -> InvestigationResult:
        iid = incident_id_of(incident)
        if not iid or iid != intent.incident_id:
            raise ValueError(f"FeedbackIntent incident_id {intent.incident_id!r} does not match incident {iid!r}")

        if review_cycle_count >= self.config.max_review_cycles:
            raise ValueError(
                f"RETRY_LIMIT_REACHED: review cycle count ({review_cycle_count}) "
                f"reached limit ({self.config.max_review_cycles})"
            )

        if intent.agent_status == "inconclusive":
            if prior_result:
                return prior_result
            return self.investigate(incident)

        targets: Set[str] = set()
        objectives: List[str] = []
        for chg in intent.requested_changes:
            t_low = (chg.target or "").lower()
            for s in SIGNALS:
                if s in t_low:
                    targets.add(s)
            objectives.append(f"Reviewer requested {chg.action} on {chg.target}: {chg.reason}")

        if not targets:
            targets = set(SIGNALS)

        if intent.human_feedback:
            objectives.append(f"Reviewer feedback: {intent.human_feedback}")

        source_run = prior_result.run_id if prior_result else "INITIAL"
        req = FollowUpRequest(
            request_id=_new_id("REQ"),
            incident_id=iid,
            origin="human_feedback",
            target_agents=sorted(list(targets)),
            objectives=objectives,
            source_run_id=source_run,
            review_cycle_id=intent.review_cycle_id,
        )
        return self.follow_up(
            req,
            incident,
            prior_result,
            reinvestigation_count=review_cycle_count,
        )

    # ---- graph --------------------------------------------------------
    def _build(self):
        g = StateGraph(InvestigationState)
        g.add_node("plan", self._plan)
        g.add_node("dispatch", self._dispatch)
        g.add_node("aggregate", self._aggregate)
        g.add_edge(START, "plan")
        g.add_edge("plan", "dispatch")
        g.add_conditional_edges("dispatch", self._route, {"dispatch": "dispatch", "aggregate": "aggregate"})
        g.add_edge("aggregate", END)
        return g.compile(checkpointer=self.checkpointer)

    def _plan(self, state):
        inc = state["incident"]
        start = inc.get("first_observed_at") or inc.get("start_time") or "unknown start"
        end = inc.get("last_observed_at") or inc.get("end_time") or "ongoing"
        services = ", ".join(inc.get("affected_services", [])) or "all services"
        window = f"for {services} from {start} to {end}"

        plan = state.get("plan")
        if not plan:
            plan = {
                "logs": [f"Find errors, exceptions and abnormal events in logs {window}"],
                "metrics": [f"Find deviations in latency, error rate and resource use {window}"],
                "traces": [f"Find slow or failed spans and the services they affect {window}"],
            }

        agents = state.get("agents")
        if not agents:
            agents = {
                s: {
                    "status": "pending",
                    "attempts": 0,
                    "findings": [],
                    "missing_evidence": [],
                    "error": None,
                }
                for s in SIGNALS
            }
        return {"plan": plan, "agents": agents}

    def _can_try(self, a) -> bool:
        return a["attempts"] < 1 + self.config.max_retries

    def _dispatch(self, state):
        agents = {s: dict(a) for s, a in state["agents"].items()}
        todo = [s for s, a in agents.items() if a["status"] in ("pending", "failed") and self._can_try(a)]
        if not todo:
            return {"agents": agents}

        incident = state["incident"]
        pool = ThreadPoolExecutor(max_workers=len(todo), thread_name_prefix="f2-agent")
        futures = {}
        for s in todo:
            task = AgentTask(
                incident=incident,
                snapshot_id=incident.get("snapshot_id"),
                run_id=state["run_id"],
                objectives=state["plan"][s],
                attempt=agents[s]["attempts"] + 1,
            )
            futures[s] = pool.submit(self.agents[s].run, task)
        done, _ = wait(futures.values(), timeout=self.config.agent_timeout_s)
        pool.shutdown(wait=False, cancel_futures=True)

        for s, fut in futures.items():
            prev = agents[s]
            if fut in done:
                res = self._collect(s, fut)
            else:
                res = AgentResult(status="failed", error=f"timed out after {self.config.agent_timeout_s:g}s")
            findings = [f.model_dump() for f in res.findings]
            if res.status == "failed" and not findings:
                findings = prev["findings"]
            agents[s] = {
                "status": res.status,
                "attempts": prev["attempts"] + 1,
                "findings": findings,
                "missing_evidence": [m.model_dump() for m in res.missing_evidence],
                "error": res.error,
            }
        return {"agents": agents}

    @staticmethod
    def _collect(signal: str, fut) -> AgentResult:
        try:
            res = AgentResult.model_validate(fut.result())
        except Exception as exc:
            return AgentResult(status="failed", error=f"{type(exc).__name__}: {exc}")
        if res.status in ("pending", "running"):
            return AgentResult(status="failed", error=f"agent returned a non-final status: {res.status}")
        wrong = [f.finding_id for f in res.findings if f.signal_type != signal]
        if wrong:
            return AgentResult(
                status="failed",
                error=f"contract violation: {signal} agent returned findings of another signal: {wrong}",
            )
        return res

    def _route(self, state):
        retry = any(a["status"] == "failed" and self._can_try(a) for a in state["agents"].values())
        return "dispatch" if retry else "aggregate"

    def _aggregate(self, state):
        outputs, missing = {}, []
        for s in SIGNALS:
            a = state["agents"][s]
            findings = [SignalFinding.model_validate(f) for f in a["findings"]]
            status = "partial" if (a["status"] == "failed" and findings) else a["status"]
            entries = [MissingEvidence.model_validate(m) for m in a["missing_evidence"]]
            if a["status"] == "failed":
                entries.append(
                    MissingEvidence(
                        required=f"{s} analysis",
                        reason=a["error"] or "agent failed",
                        requested_from=s,
                        status="failed",
                    )
                )
            elif status in ("partial", "inconclusive") and not entries:
                entries.append(
                    MissingEvidence(
                        required=f"complete {s} evidence",
                        requested_from=s,
                        status="unavailable",
                        reason=a["error"] or f"{s} agent returned {status} without saying what is missing",
                    )
                )
            missing += entries
            outputs[s] = SignalOutput(findings=findings, status=status)

        statuses = [o.status for o in outputs.values()]
        total = sum(len(o.findings) for o in outputs.values())
        if all(x == "completed" for x in statuses) and not missing:
            overall = "completed"
        elif total == 0:
            overall = "failed" if "failed" in statuses else "inconclusive"
        else:
            overall = "partial"

        result = InvestigationResult(
            incident_id=incident_id_of(state["incident"]),
            run_id=state["run_id"],
            logs_output=outputs["logs"],
            metrics_output=outputs["metrics"],
            traces_output=outputs["traces"],
            missing_evidence=missing,
            status=overall,
            investigation_plan_id=state["plan_id"],
            parent_run_id=state.get("parent_run_id"),
        )
        return {"result": result.model_dump()}
