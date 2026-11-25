"""F6-owned tables. They mirror review_service migration 0001 exactly
(review_service/tests/test_schema_parity.py checks this); the migration adds the
immutability trigger on review_decisions."""
from sqlalchemy import CheckConstraint, Column, ForeignKey, ForeignKeyConstraint, Integer, MetaData, Table, Text, \
    UniqueConstraint

metadata = MetaData()

review_cycles = Table(
    "review_cycles", metadata,
    Column("review_cycle_id", Text, primary_key=True),
    Column("report_id", Text, nullable=False),
    Column("report_version", Integer, nullable=False),
    Column("cycle_number", Integer, nullable=False),
    Column("state", Text, nullable=False),
    Column("state_version", Integer, nullable=False, server_default="0"),
    Column("opened_at", Text),
    Column("closed_at", Text),
    ForeignKeyConstraint(["report_id", "report_version"], ["report_versions.report_id", "report_versions.version"]),
    UniqueConstraint("report_id", "report_version", name="uq_review_cycles_version"),
)

review_decisions = Table(
    "review_decisions", metadata,
    Column("decision_id", Text, primary_key=True),
    Column("idempotency_key", Text, nullable=False, unique=True),
    Column("request_hash", Text, nullable=False),
    Column("incident_id", Text, nullable=False),
    Column("report_id", Text, nullable=False),
    Column("version", Integer, nullable=False),
    Column("review_cycle_id", Text, ForeignKey("review_cycles.review_cycle_id"), nullable=False),
    Column("reviewer_id", Text, nullable=False),
    Column("decision", Text, nullable=False),
    Column("feedback", Text, nullable=True),
    Column("decided_at", Text, nullable=False),
)

feedback_intents = Table(
    "feedback_intents", metadata,
    Column("feedback_id", Text, primary_key=True),
    Column("decision_id", Text, ForeignKey("review_decisions.decision_id"), nullable=False, unique=True),
    Column("review_cycle_id", Text, nullable=False),
    Column("human_feedback", Text, nullable=False),
    Column("summary_of_human_intent", Text, nullable=True),
    Column("requested_changes", Text, nullable=False),
    Column("agent_status", Text, nullable=False),
    Column("interpreter", Text, nullable=False),
    Column("attempts", Integer, nullable=False),
    Column("raw_output", Text, nullable=True),
    Column("created_at", Text),
)

outbox_events = Table(
    "outbox_events", metadata,
    Column("event_id", Text, primary_key=True),
    Column("event_type", Text, nullable=False),
    Column("dedupe_key", Text, nullable=False),
    Column("payload", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("attempts", Integer, nullable=False, server_default="0"),
    Column("next_attempt_at", Text, nullable=False),
    Column("claimed_until", Text),
    Column("last_error", Text, nullable=True),
    Column("created_at", Text),
    Column("delivered_at", Text),
    UniqueConstraint("event_type", "dedupe_key", name="uq_outbox_events_dedupe"),
    CheckConstraint("status IN ('pending','delivered','failed')", name="ck_outbox_events_status"),
)

reinvestigations = Table(
    "reinvestigations", metadata,
    Column("request_id", Text, primary_key=True),
    Column("review_cycle_id", Text, nullable=False, unique=True),
    Column("feedback_id", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("f2_run_id", Text, nullable=True),
    Column("last_error", Text, nullable=True),
    Column("created_at", Text),
    Column("updated_at", Text),
)

review_audit = Table(
    "review_audit", metadata,
    Column("audit_id", Integer, primary_key=True, autoincrement=True),
    Column("at", Text, nullable=False),
    Column("actor", Text, nullable=True),
    Column("action", Text, nullable=False),
    Column("report_id", Text, nullable=True),
    Column("version", Integer, nullable=True),
    Column("outcome", Text, nullable=False),
    Column("reason_code", Text, nullable=True),
    Column("detail", Text, nullable=True),
)
