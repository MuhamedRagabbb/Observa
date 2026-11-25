"""Investigation Orchestrator (F2-US-04): plan -> dispatch -> (retry) -> aggregate,
with LangGraph checkpointer persistence, FollowUpRequest (F3), and FeedbackIntent (F6)."""
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, wait
from typing import Any, Dict, List, Optional, Set, Tuple

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy, Send

from .checkpointer import SqliteSaver
from .agents.base import AgentResult, AgentTask, SignalAgent
from .config import InvestigationConfig
from .contracts import (
    F2Error,
    ErrorEnvelope,
    IncidentV1,
    SIGNALS,
    EvidenceAnswer,
    FeedbackIntent,
    FollowUpRequest,
    InvestigationResult,
    MissingEvidence,
    SignalFinding,
    SignalOutput,
)
from .state import InvestigationState


def validate_incident(incident: Any) -> IncidentV1:
    """Validate inbound incident payload at the F2 boundary (F2-6)."""
    if isinstance(incident, IncidentV1):
        return incident
    if isinstance(incident, dict):
        raw = dict(incident)
        if "id" in raw and "incident_id" not in raw:
            raw["incident_id"] = raw["id"]
        if "first_observed_at" in raw and "start_time" not in raw:
            raw["start_time"] = raw["first_observed_at"]
        if "last_observed_at" in raw and "end_time" not in raw:
            raw["end_time"] = raw["last_observed_at"]
        if isinstance(raw.get("alerts"), dict):
            raw["alerts"] = sorted(list(raw["alerts"].keys()))
        if not raw.get("incident_id"):
            raise F2Error(
                ErrorEnvelope(
                    code="VALIDATION_ERROR",
                    message="incident payload is missing required 'incident_id'",
                    retryable=False,
                    operation_id="VALIDATE_INCIDENT",
                )
            )
        try:
            return IncidentV1.model_validate(raw)
        except Exception as e:
            raise F2Error(
                ErrorEnvelope(
                    code="VALIDATION_ERROR",
                    message=f"malformed incident payload: {e}",
                    retryable=False,
                    operation_id="VALIDATE_INCIDENT",
                )
            )
    raise F2Error(
        ErrorEnvelope(
            code="VALIDATION_ERROR",
            message=f"expected dict or IncidentV1, got {type(incident).__name__}",
            retryable=False,
            operation_id="VALIDATE_INCIDENT",
        )
    )


def incident_id_of(incident: Any) -> str:
    inc_obj = validate_incident(incident)
    return inc_obj.incident_id


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


class _RetryableAgentError(RuntimeError):
    """Internal exception raised so LangGraph RetryPolicy performs backoff and retry."""


class Orchestrator:
    def __init__(
        self,
        agents: Dict[str, SignalAgent],
        config: Optional[InvestigationConfig] = None,
        checkpointer: Optional[Any] = None,
    ):
        absent = [s for s in SIGNALS if s not in agents]
        if absent:
            raise F2Error(
                ErrorEnvelope(
                    code="VALIDATION_ERROR",
                    message=f"no agent registered for: {absent}",
                    retryable=False,
                    operation_id="INIT",
                )
            )
        self.agents = agents
        self.config = config or InvestigationConfig()
        self.checkpointer = checkpointer if checkpointer is not None else SqliteSaver()
        self._lock = threading.Lock()
        self._run_attempts: Dict[Tuple[str, str], int] = {}
        self._last_partial: Dict[Tuple[str, str], Tuple[List[dict], List[dict]]] = {}
        self.graph = self._build()

    def resume(self, run_id: str) -> InvestigationResult:
        """Resume or retrieve an investigation from durable SQLite checkpoint (F2-4)."""
        cfg = {"configurable": {"thread_id": run_id}}
        state = self.graph.get_state(cfg)
        if not state or not state.values:
            raise F2Error(
                ErrorEnvelope(
                    code="VALIDATION_ERROR",
                    message=f"no checkpoint found for run_id {run_id}",
                    retryable=False,
                    operation_id=run_id,
                )
            )
        if "result" in state.values and state.values["result"] is not None:
            return InvestigationResult.model_validate(state.values["result"])
        final = self.graph.invoke(None, config=cfg)
        return InvestigationResult.model_validate(final["result"])

    # ---- public: initial investigation --------------------------------
    def investigate(
        self,
        incident: Any,
        *,
        run_id: Optional[str] = None,
        plan_id: Optional[str] = None,
        parent_run_id: Optional[str] = None,
    ) -> InvestigationResult:
        valid_inc = validate_incident(incident)
        if not valid_inc.incident_id:
            raise F2Error(
                ErrorEnvelope(
                    code="VALIDATION_ERROR",
                    message="incident has no incident_id",
                    retryable=False,
                    operation_id=run_id or "INITIAL",
                )
            )
        actual_run_id = run_id or _new_id("RUN")
        cfg = {"configurable": {"thread_id": actual_run_id}}
        final = self.graph.invoke(
            {
                "incident": valid_inc.model_dump(),
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
        review_cycle_count: Optional[int] = None,
    ) -> InvestigationResult:
        iid = incident_id_of(incident)
        if not iid or iid != request.incident_id:
            raise F2Error(
                ErrorEnvelope(
                    code="VALIDATION_ERROR",
                    message=f"FollowUpRequest incident_id {request.incident_id!r} does not match incident {iid!r}",
                    retryable=False,
                    operation_id=request.request_id,
                )
            )

        if request.origin == "human_feedback":
            eff_review = review_cycle_count if review_cycle_count is not None else 0
            if eff_review >= self.config.max_review_cycles:
                raise F2Error(
                    ErrorEnvelope(
                        code="RETRY_LIMIT_REACHED",
                        message=f"review cycle count ({eff_review}) reached limit ({self.config.max_review_cycles})",
                        retryable=False,
                        operation_id=request.request_id,
                    )
                )
        else:
            if reinvestigation_count >= self.config.max_reinvestigations:
                raise F2Error(
                    ErrorEnvelope(
                        code="RETRY_LIMIT_REACHED",
                        message=f"re-investigation count ({reinvestigation_count}) reached limit ({self.config.max_reinvestigations})",
                        retryable=False,
                        operation_id=request.request_id,
                    )
                )

        targets: Set[str] = set(request.target_agents) if request.target_agents else set(SIGNALS)
        invalid = targets - set(SIGNALS)
        if invalid:
            raise F2Error(
                ErrorEnvelope(
                    code="VALIDATION_ERROR",
                    message=f"invalid target_agents in FollowUpRequest: {sorted(invalid)}",
                    retryable=False,
                    operation_id=request.request_id,
                )
            )

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
                    "answers": [a.model_dump() for a in out.answers],
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

        valid_inc = validate_incident(incident)
        inc_dict = valid_inc.model_dump()
        start = valid_inc.start_time or "unknown start"
        end = valid_inc.end_time or "ongoing"
        services = ", ".join(valid_inc.affected_services) or "all services"
        window = f"for {services} from {start} to {end}"

        plan = {
            "logs": [f"Find errors, exceptions and abnormal events in logs {window}"],
            "metrics": [f"Find deviations in latency, error rate and resource use {window}"],
            "traces": [f"Find slow or failed spans and the services they affect {window}"],
        }
        for s in targets:
            if request.objectives:
                plan[s] = list(request.signal_objectives.get(s, request.objectives))

        run_id = _new_id("RUN")
        plan_id = _new_id("PLAN")
        cfg = {"configurable": {"thread_id": run_id}}

        final = self.graph.invoke(
            {
                "incident": inc_dict,
                "run_id": run_id,
                "plan_id": plan_id,
                "parent_run_id": request.source_run_id,
                "follow_up_targets": sorted(targets),
                "plan": plan,
                "agents": agents_init,
            },
            config=cfg,
        )
        result = InvestigationResult.model_validate(final["result"])
        if prior_result:
            for s in targets:
                out = getattr(result, f"{s}_output")
                prior = getattr(prior_result, f"{s}_output")
                combined = {f.finding_id: f for f in [*prior.findings, *out.findings]}
                out.findings = list(combined.values())
                if not out.answers and out.status == "failed":
                    for question in plan[s]:
                        out.answers.append(EvidenceAnswer(signal=s, snapshot_id=valid_inc.snapshot_id,
                                           question=question, answer="Unable to read or analyze the retained evidence."))
                out.answers = [*prior.answers, *out.answers]
                if out.findings and out.status in ("failed", "inconclusive"):
                    out.status = "partial"
        return result

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
            raise F2Error(
                ErrorEnvelope(
                    code="VALIDATION_ERROR",
                    message=f"FeedbackIntent incident_id {intent.incident_id!r} does not match incident {iid!r}",
                    retryable=False,
                    operation_id=intent.feedback_id,
                )
            )

        if review_cycle_count >= self.config.max_review_cycles:
            raise F2Error(
                ErrorEnvelope(
                    code="RETRY_LIMIT_REACHED",
                    message=f"review cycle count ({review_cycle_count}) reached limit ({self.config.max_review_cycles})",
                    retryable=False,
                    operation_id=intent.feedback_id,
                )
            )

        if intent.agent_status == "inconclusive":
            if prior_result:
                return prior_result
            raise F2Error(
                ErrorEnvelope(
                    code="VALIDATION_ERROR",
                    message="Ambiguous feedback with no prior result; auto-reinvestigation is prohibited",
                    retryable=False,
                    operation_id=intent.feedback_id,
                )
            )

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
            review_cycle_count=review_cycle_count,
        )

    # ---- graph --------------------------------------------------------
    def _build(self):
        retry_policy = RetryPolicy(
            initial_interval=0.1,
            backoff_factor=2.0,
            max_interval=2.0,
            max_attempts=1 + self.config.max_retries,
            jitter=False,
            retry_on=_RetryableAgentError,
        )
        g = StateGraph(InvestigationState)
        g.add_node("plan", self._plan)
        for s in SIGNALS:
            g.add_node(f"run_{s}", self._make_signal_node(s), retry_policy=retry_policy)
        g.add_node("aggregate", self._aggregate)
        g.add_edge(START, "plan")
        g.add_conditional_edges(
            "plan",
            self._fan_out,
            [f"run_{s}" for s in SIGNALS] + ["aggregate"],
        )
        for s in SIGNALS:
            g.add_edge(f"run_{s}", "aggregate")
        g.add_edge("aggregate", END)
        return g.compile(checkpointer=self.checkpointer)

    def _plan(self, state):
        valid_inc = validate_incident(state["incident"])
        start = valid_inc.start_time or "unknown start"
        end = valid_inc.end_time or "ongoing"
        services = ", ".join(valid_inc.affected_services) or "all services"
        window = f"for {services} from {start} to {end}"

        plan = state.get("plan")
        if not plan:
            plan = {
                "logs": [f"Find errors, exceptions and abnormal events in logs {window}"],
                "metrics": [f"Find deviations in latency, error rate and resource use {window}"],
                "traces": [f"Find slow or failed spans and the services they affect {window}"],
            }

        agents = dict(state.get("agents") or {})
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

        targets = set(state.get("follow_up_targets", SIGNALS))
        for s in SIGNALS:
            if s in targets and agents[s]["status"] in ("pending", "failed") and self._can_try(agents[s]):
                agents[s] = {**agents[s], "status": "running"}

        return {"plan": plan, "agents": agents}

    def _can_try(self, a) -> bool:
        return a["attempts"] < 1 + self.config.max_retries

    def _fan_out(self, state):
        targets = state.get("follow_up_targets", list(SIGNALS))
        sends = [
            Send(f"run_{s}", state)
            for s in targets
            if state["agents"][s]["status"] == "running"
        ]
        return sends if sends else "aggregate"

    def _next_attempt(self, run_id: str, signal: str, base_attempts: int) -> int:
        key = (run_id, signal)
        with self._lock:
            current = self._run_attempts.get(key, base_attempts) + 1
            self._run_attempts[key] = current
            return current

    def _make_signal_node(self, signal: str):
        def _run_signal(state: InvestigationState):
            run_id = state["run_id"]
            prev = state["agents"][signal]
            attempt = self._next_attempt(run_id, signal, prev.get("attempts", 0))
            incident = state["incident"]
            task = AgentTask(
                incident=incident,
                snapshot_id=incident.get("snapshot_id"),
                run_id=run_id,
                objectives=state["plan"][signal],
                follow_up=signal in state.get("follow_up_targets", []),
                attempt=attempt,
            )

            pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"f2-{signal}")
            fut = pool.submit(self.agents[signal].run, task)
            done, _ = wait([fut], timeout=self.config.agent_timeout_s)
            pool.shutdown(wait=False, cancel_futures=True)

            if fut in done:
                res = self._collect(signal, fut)
            else:
                res = AgentResult(status="failed", error=f"timed out after {self.config.agent_timeout_s:g}s")

            findings = [f.model_dump() for f in res.findings]
            missing_evidence = [m.model_dump() for m in res.missing_evidence]
            key = (run_id, signal)

            if findings:
                with self._lock:
                    self._last_partial[key] = (findings, missing_evidence)
            elif res.status == "failed":
                with self._lock:
                    saved = self._last_partial.get(key)
                if saved:
                    findings, missing_evidence = saved
                elif prev.get("findings"):
                    findings = prev["findings"]
                    missing_evidence = prev.get("missing_evidence", [])

            if res.status == "failed" and attempt < 1 + self.config.max_retries:
                raise _RetryableAgentError(res.error or f"{signal} agent failed")

            with self._lock:
                self._run_attempts.pop(key, None)
                self._last_partial.pop(key, None)

            return {
                "agents": {
                    signal: {
                        "status": res.status,
                        "answers": [a.model_dump() for a in res.answers],
                        "attempts": attempt,
                        "findings": findings,
                        "missing_evidence": missing_evidence,
                        "error": res.error,
                    }
                }
            }

        return _run_signal

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
            outputs[s] = SignalOutput(findings=findings, status=status, answers=a.get("answers", []))

        statuses = [o.status for o in outputs.values()]
        total = sum(len(o.findings) for o in outputs.values())
        if all(x == "completed" for x in statuses) and not missing:
            overall = "completed"
        elif all(x == "failed" for x in statuses):
            overall = "failed"
        elif total == 0 and "completed" not in statuses:
            overall = "inconclusive"
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
