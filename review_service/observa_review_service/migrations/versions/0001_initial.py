"""Initial F6/F7 schema: report versions, review state, outbox and audit.

Portable across SQLite and PostgreSQL: TEXT/INTEGER columns, JSON stored as
TEXT, timestamps as UTC ISO-8601 TEXT. Immutable tables are protected by
triggers so no code path can rewrite a report version or a review decision.

Revision ID: 0001_initial
Revises:
Create Date: 2026-11-25
"""
from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

IMMUTABLE = ("report_contents", "report_versions", "review_decisions")


def _ts(name: str, nullable: bool = True) -> sa.Column:
    return sa.Column(name, sa.Text, nullable=nullable)


def upgrade() -> None:
    # ---- F7-owned -----------------------------------------------------------
    op.create_table(
        "report_heads",
        sa.Column("report_id", sa.Text, primary_key=True),
        sa.Column("incident_id", sa.Text, nullable=False, unique=True),
        sa.Column("current_version", sa.Integer, nullable=False),
        _ts("created_at"), _ts("updated_at"),
        sa.CheckConstraint("current_version >= 1", name="ck_report_heads_version"),
    )
    op.create_table(
        "report_contents",
        sa.Column("content_digest", sa.Text, primary_key=True),
        sa.Column("schema_version", sa.Text, nullable=False),
        sa.Column("body_json", sa.Text, nullable=False),
        _ts("created_at"),
    )
    op.create_table(
        "report_versions",
        sa.Column("report_id", sa.Text, sa.ForeignKey("report_heads.report_id"), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("previous_version", sa.Integer, nullable=True),
        sa.Column("content_digest", sa.Text, sa.ForeignKey("report_contents.content_digest"), nullable=False),
        sa.Column("source_investigation_run_id", sa.Text, nullable=False),
        sa.Column("source_correlation_run_id", sa.Text, nullable=True),
        sa.Column("revision_of_cycle_id", sa.Text, nullable=True),
        sa.Column("evidence_ids", sa.Text, nullable=False),
        sa.Column("uncertainty_flags", sa.Text, nullable=False),
        _ts("created_at", nullable=False),
        sa.PrimaryKeyConstraint("report_id", "version"),
        sa.CheckConstraint("version >= 1", name="ck_report_versions_version"),
        sa.CheckConstraint(
            "(version = 1 AND previous_version IS NULL) OR previous_version = version - 1",
            name="ck_report_versions_lineage"),
        sa.UniqueConstraint("report_id", "source_investigation_run_id", "content_digest",
                            name="uq_report_versions_republish"),
    )

    # ---- F6-owned -----------------------------------------------------------
    op.create_table(
        "review_cycles",
        sa.Column("review_cycle_id", sa.Text, primary_key=True),
        sa.Column("report_id", sa.Text, nullable=False),
        sa.Column("report_version", sa.Integer, nullable=False),
        sa.Column("cycle_number", sa.Integer, nullable=False),
        sa.Column("state", sa.Text, nullable=False),
        sa.Column("state_version", sa.Integer, nullable=False, server_default="0"),
        _ts("opened_at"), _ts("closed_at"),
        sa.ForeignKeyConstraint(["report_id", "report_version"],
                                ["report_versions.report_id", "report_versions.version"]),
        sa.UniqueConstraint("report_id", "report_version", name="uq_review_cycles_version"),
        sa.CheckConstraint(
            "state IN ('pending_review','approved','changes_requested','interpreting',"
            "'clarification_needed','reinvestigating','superseded','revision_failed','limit_reached')",
            name="ck_review_cycles_state"),
    )
    op.create_table(
        "review_decisions",
        sa.Column("decision_id", sa.Text, primary_key=True),
        sa.Column("idempotency_key", sa.Text, nullable=False, unique=True),
        sa.Column("request_hash", sa.Text, nullable=False),
        sa.Column("incident_id", sa.Text, nullable=False),
        sa.Column("report_id", sa.Text, nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("review_cycle_id", sa.Text, sa.ForeignKey("review_cycles.review_cycle_id"), nullable=False),
        sa.Column("reviewer_id", sa.Text, nullable=False),
        sa.Column("decision", sa.Text, nullable=False),
        sa.Column("feedback", sa.Text, nullable=True),
        _ts("decided_at", nullable=False),
        sa.CheckConstraint("decision IN ('approve','request_changes')", name="ck_review_decisions_decision"),
        sa.CheckConstraint(
            "(decision = 'approve' AND feedback IS NULL) OR "
            "(decision = 'request_changes' AND feedback IS NOT NULL AND length(trim(feedback)) > 0)",
            name="ck_review_decisions_feedback"),
    )
    op.create_table(
        "feedback_intents",
        sa.Column("feedback_id", sa.Text, primary_key=True),
        sa.Column("decision_id", sa.Text, sa.ForeignKey("review_decisions.decision_id"),
                  nullable=False, unique=True),
        sa.Column("review_cycle_id", sa.Text, nullable=False),
        sa.Column("human_feedback", sa.Text, nullable=False),
        sa.Column("summary_of_human_intent", sa.Text, nullable=True),
        sa.Column("requested_changes", sa.Text, nullable=False),
        sa.Column("agent_status", sa.Text, nullable=False),
        sa.Column("interpreter", sa.Text, nullable=False),
        sa.Column("attempts", sa.Integer, nullable=False),
        sa.Column("raw_output", sa.Text, nullable=True),
        _ts("created_at"),
    )
    op.create_table(
        "outbox_events",
        sa.Column("event_id", sa.Text, primary_key=True),
        sa.Column("event_type", sa.Text, nullable=False),
        sa.Column("dedupe_key", sa.Text, nullable=False),
        sa.Column("payload", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        _ts("next_attempt_at", nullable=False), _ts("claimed_until"),
        sa.Column("last_error", sa.Text, nullable=True),
        _ts("created_at"), _ts("delivered_at"),
        sa.UniqueConstraint("event_type", "dedupe_key", name="uq_outbox_events_dedupe"),
        sa.CheckConstraint("status IN ('pending','delivered','failed')", name="ck_outbox_events_status"),
    )
    op.create_index("ix_outbox_events_due", "outbox_events", ["status", "next_attempt_at"])
    op.create_table(
        "reinvestigations",
        sa.Column("request_id", sa.Text, primary_key=True),
        sa.Column("review_cycle_id", sa.Text, nullable=False, unique=True),
        sa.Column("feedback_id", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("f2_run_id", sa.Text, nullable=True),
        sa.Column("last_error", sa.Text, nullable=True),
        _ts("created_at"), _ts("updated_at"),
        sa.CheckConstraint("status IN ('requested','completed','failed')", name="ck_reinvestigations_status"),
    )
    op.create_table(
        "review_audit",
        sa.Column("audit_id", sa.Integer, primary_key=True, autoincrement=True),
        _ts("at", nullable=False),
        sa.Column("actor", sa.Text, nullable=True),
        sa.Column("action", sa.Text, nullable=False),
        sa.Column("report_id", sa.Text, nullable=True),
        sa.Column("version", sa.Integer, nullable=True),
        sa.Column("outcome", sa.Text, nullable=False),
        sa.Column("reason_code", sa.Text, nullable=True),
        sa.Column("detail", sa.Text, nullable=True),
        sa.CheckConstraint("outcome IN ('accepted','replayed','rejected')", name="ck_review_audit_outcome"),
    )
    _create_immutability_triggers()


def _create_immutability_triggers() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        for table in IMMUTABLE:
            for action in ("UPDATE", "DELETE"):
                op.execute(
                    f"CREATE TRIGGER trg_{table}_no_{action.lower()} BEFORE {action} ON {table} "
                    f"BEGIN SELECT RAISE(ABORT, '{table} is immutable'); END")
    elif dialect == "postgresql":
        op.execute(
            "CREATE FUNCTION observa_reject_mutation() RETURNS trigger LANGUAGE plpgsql AS $$ "
            "BEGIN RAISE EXCEPTION '% is immutable', TG_TABLE_NAME; END $$")
        for table in IMMUTABLE:
            op.execute(
                f"CREATE TRIGGER trg_{table}_immutable BEFORE UPDATE OR DELETE ON {table} "
                f"FOR EACH ROW EXECUTE FUNCTION observa_reject_mutation()")
    else:
        raise RuntimeError(f"Immutability triggers are not defined for dialect {dialect!r}")


def downgrade() -> None:
    raise RuntimeError("0001_initial is the base schema; downgrading would discard immutable history")
