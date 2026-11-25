"""F7-owned tables. They mirror review_service migration 0001 exactly
(review_service/tests/test_schema_parity.py checks this); the migration adds the
immutability triggers."""
from sqlalchemy import CheckConstraint, Column, ForeignKey, Integer, MetaData, PrimaryKeyConstraint, Table, \
    Text, UniqueConstraint

metadata = MetaData()

report_heads = Table(
    "report_heads", metadata,
    Column("report_id", Text, primary_key=True),
    Column("incident_id", Text, nullable=False, unique=True),
    Column("current_version", Integer, nullable=False),
    Column("created_at", Text),
    Column("updated_at", Text),
    CheckConstraint("current_version >= 1", name="ck_report_heads_version"),
)

report_contents = Table(
    "report_contents", metadata,
    Column("content_digest", Text, primary_key=True),
    Column("schema_version", Text, nullable=False),
    Column("body_json", Text, nullable=False),
    Column("created_at", Text),
)

report_versions = Table(
    "report_versions", metadata,
    Column("report_id", Text, ForeignKey("report_heads.report_id"), nullable=False),
    Column("version", Integer, nullable=False),
    Column("previous_version", Integer, nullable=True),
    Column("content_digest", Text, ForeignKey("report_contents.content_digest"), nullable=False),
    Column("source_investigation_run_id", Text, nullable=False),
    Column("source_correlation_run_id", Text, nullable=True),
    Column("revision_of_cycle_id", Text, nullable=True),
    Column("evidence_ids", Text, nullable=False),
    Column("uncertainty_flags", Text, nullable=False),
    Column("created_at", Text, nullable=False),
    PrimaryKeyConstraint("report_id", "version"),
    CheckConstraint("version >= 1", name="ck_report_versions_version"),
    CheckConstraint("(version = 1 AND previous_version IS NULL) OR previous_version = version - 1",
                    name="ck_report_versions_lineage"),
    UniqueConstraint("report_id", "source_investigation_run_id", "content_digest",
                     name="uq_report_versions_republish"),
)
