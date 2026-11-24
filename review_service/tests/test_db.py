"""Task 0.1: migration, immutability triggers and the unit of work."""
import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import DatabaseError

from observa_review_service.db import make_engine, unit_of_work, upgrade

ALL_TABLES = {
    "report_heads", "report_contents", "report_versions",
    "review_cycles", "review_decisions", "feedback_intents",
    "outbox_events", "reinvestigations", "review_audit",
}
NOW = "2026-11-25T10:00:00Z"


@pytest.fixture()
def engine(tmp_path):
    url = f"sqlite:///{(tmp_path / 'review.sqlite3').as_posix()}"
    upgrade(url)
    eng = make_engine(url)
    yield eng
    eng.dispose()


def seed(conn):
    conn.execute(text("INSERT INTO report_heads VALUES ('RPT-1','INC-1',1,:t,:t)"), {"t": NOW})
    conn.execute(text("INSERT INTO report_contents VALUES ('d1','1.0','{}',:t)"), {"t": NOW})
    conn.execute(text(
        "INSERT INTO report_versions (report_id, version, previous_version, content_digest,"
        " source_investigation_run_id, evidence_ids, uncertainty_flags, created_at)"
        " VALUES ('RPT-1',1,NULL,'d1','RUN-1','[]','[]',:t)"), {"t": NOW})
    conn.execute(text(
        "INSERT INTO review_cycles (review_cycle_id, report_id, report_version, cycle_number, state, opened_at)"
        " VALUES ('CYCLE-1','RPT-1',1,1,'pending_review',:t)"), {"t": NOW})
    conn.execute(text(
        "INSERT INTO review_decisions VALUES ('DEC-1','key-1','h1','INC-1','RPT-1',1,'CYCLE-1',"
        "'alice','approve',NULL,:t)"), {"t": NOW})


def test_migration_creates_all_tables(engine):
    assert ALL_TABLES <= set(inspect(engine).get_table_names())


@pytest.mark.parametrize("statement", [
    "UPDATE report_versions SET source_investigation_run_id='RUN-X'",
    "DELETE FROM report_versions",
    "UPDATE report_contents SET body_json='tampered'",
    "DELETE FROM report_contents",
    "UPDATE review_decisions SET decision='request_changes', feedback='x'",
    "DELETE FROM review_decisions",
])
def test_immutable_tables_reject_update_and_delete(engine, statement):
    with unit_of_work(engine) as conn:
        seed(conn)
    with pytest.raises(DatabaseError, match="immutable"):
        with unit_of_work(engine) as conn:
            conn.execute(text(statement))
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM report_versions")).scalar() == 1
        assert conn.execute(text("SELECT body_json FROM report_contents")).scalar() == "{}"
        assert conn.execute(text("SELECT decision FROM review_decisions")).scalar() == "approve"


def test_unit_of_work_rolls_back_on_exception(engine):
    with pytest.raises(RuntimeError, match="boom"):
        with unit_of_work(engine) as conn:
            conn.execute(text("INSERT INTO report_heads VALUES ('RPT-2','INC-2',1,:t,:t)"), {"t": NOW})
            raise RuntimeError("boom")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM report_heads")).scalar() == 0


def test_decision_feedback_rule_enforced_by_schema(engine):
    with unit_of_work(engine) as conn:
        seed(conn)
    with pytest.raises(DatabaseError):
        with unit_of_work(engine) as conn:
            conn.execute(text(
                "INSERT INTO review_decisions VALUES ('DEC-2','key-2','h2','INC-1','RPT-1',1,'CYCLE-1',"
                "'bob','request_changes','   ',:t)"), {"t": NOW})


def test_sqlite_engine_uses_wal_foreign_keys_and_immediate_transactions(engine):
    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1
    with pytest.raises(DatabaseError):  # FK enforced: version for an unknown report
        with unit_of_work(engine) as conn:
            conn.execute(text(
                "INSERT INTO report_versions (report_id, version, previous_version, content_digest,"
                " source_investigation_run_id, evidence_ids, uncertainty_flags, created_at)"
                " VALUES ('RPT-missing',1,NULL,'nope','RUN-1','[]','[]',:t)"), {"t": NOW})
