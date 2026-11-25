"""Task 2.1: immutable, content-addressed report versions with compare-and-swap (F7-US-03)."""
import hashlib
import threading
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from observa_reports.assemble import assemble
from observa_reports.content import digest
from observa_reports.errors import ReportError
from observa_reports.service import ReportService, RevisionRef

from f7_helpers import bundle, uow

PAYMENTS = "INC-6558d5b7036a44934e9e"
REPORT_ID = "RPT-" + hashlib.sha256(PAYMENTS.encode()).hexdigest()[:16]
T0 = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)


def service(when=T0):
    return ReportService(clock=lambda: when)


def count(engine, table):
    with engine.connect() as conn:
        return conn.execute(text(f"SELECT count(*) FROM {table}")).scalar()


def head(engine):
    with engine.connect() as conn:
        return conn.execute(text("SELECT current_version FROM report_heads WHERE report_id=:r"),
                            {"r": REPORT_ID}).scalar()


def publish_v1(engine):
    with uow(engine) as conn:
        return service().publish(conn, bundle("payments_v1"))


def test_first_publish_creates_v1_with_stable_report_id(engine):
    record = publish_v1(engine)
    assert (record.report_id, record.incident_id, record.version, record.previous_version) == \
        (REPORT_ID, PAYMENTS, 1, None)
    expected_digest = digest(assemble(bundle("payments_v1")))
    assert (record.content_digest, record.content_ref) == (expected_digest, f"sha256:{expected_digest}")
    assert record.source_investigation_run_id == "RUN-2ccc9df9"
    assert record.source_correlation_run_id == "RUN-b161df39a5b71ec5"
    assert record.created_at == T0 and record.review_cycle_id is None
    assert "INC-6558d5b7036a44934e9e/r1-86bc3f518d9d66dfa96f466c9832cd2ebf260f3062dccb055af2cefe36ab9f80/logs#P1" \
        in record.evidence_ids
    assert head(engine) == 1


def test_republish_same_run_and_digest_returns_existing_version(engine):
    first = publish_v1(engine)
    with uow(engine) as conn:
        again = service(datetime(2026, 11, 26, tzinfo=timezone.utc)).publish(conn, bundle("payments_v1"))
    assert again == first
    assert (count(engine, "report_versions"), count(engine, "report_contents")) == (1, 1)


def test_publishing_new_content_without_a_revision_ref_conflicts(engine):
    publish_v1(engine)
    with pytest.raises(ReportError) as err, uow(engine) as conn:
        service().publish(conn, bundle("payments_v2_revised"))
    assert err.value.envelope.code == "VERSION_CONFLICT"
    assert head(engine) == 1


def test_revision_creates_v2_linked_to_v1_and_v1_unchanged(engine):
    v1 = publish_v1(engine)
    with uow(engine) as conn:
        v2 = service().publish(conn, bundle("payments_v2_revised"),
                               revision_of=RevisionRef(review_cycle_id="CYCLE-abc", expected_previous=1))
    assert (v2.version, v2.previous_version, v2.review_cycle_id) == (2, 1, "CYCLE-abc")
    assert v2.content_digest != v1.content_digest
    with engine.connect() as conn:
        stored_v1 = service().get(conn, REPORT_ID, 1)
        current = service().get(conn, REPORT_ID)
    assert stored_v1.record == v1
    assert stored_v1.content == assemble(bundle("payments_v1"))
    assert current.record == v2 and head(engine) == 2


def test_stale_expected_previous_conflicts(engine):
    publish_v1(engine)
    with uow(engine) as conn:
        service().publish(conn, bundle("payments_v2_revised"), revision_of=RevisionRef(review_cycle_id=None,
                                                                                         expected_previous=1))
    with pytest.raises(ReportError) as err, uow(engine) as conn:
        service().publish(conn, bundle("missing_f4"),
                          revision_of=RevisionRef(review_cycle_id="CYCLE-late", expected_previous=1))
    assert err.value.envelope.code == "VERSION_CONFLICT"
    assert "expected_previous" in err.value.envelope.message
    assert (head(engine), count(engine, "report_versions")) == (2, 2)


def test_revision_of_unknown_report_conflicts(engine):
    with pytest.raises(ReportError) as err, uow(engine) as conn:
        service().publish(conn, bundle("payments_v1"), revision_of=RevisionRef(review_cycle_id=None,
                                                                                expected_previous=1))
    assert err.value.envelope.code == "VERSION_CONFLICT"
    assert count(engine, "report_heads") == 0


def test_two_concurrent_revisions_one_wins(engine):
    publish_v1(engine)
    barrier, outcomes = threading.Barrier(2), []

    def revise(name, cycle):
        barrier.wait()
        try:
            with uow(engine) as conn:
                outcomes.append(("ok", service().publish(conn, bundle(name), revision_of=RevisionRef(
                    review_cycle_id=cycle, expected_previous=1)).version))
        except ReportError as exc:
            outcomes.append(("conflict", exc.envelope.code))

    threads = [threading.Thread(target=revise, args=("payments_v2_revised", "CYCLE-a")),
               threading.Thread(target=revise, args=("missing_f4", "CYCLE-b"))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(outcomes) == [("conflict", "VERSION_CONFLICT"), ("ok", 2)]
    assert (head(engine), count(engine, "report_versions")) == (2, 2)


def test_cas_rejects_a_head_moved_by_a_concurrent_writer(engine):
    """Under Postgres read-committed another writer can commit between our read and our update;
    the head UPDATE ... WHERE current_version = :previous must then match nothing and abort."""
    from sqlalchemy import event
    publish_v1(engine)
    moved = {"done": False}

    def concurrent_writer(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("UPDATE REPORT_HEADS") and not moved["done"]:
            moved["done"] = True
            cursor.execute("UPDATE report_heads SET current_version = 2 WHERE report_id = ?", (REPORT_ID,))

    event.listen(engine, "before_cursor_execute", concurrent_writer)
    try:
        with pytest.raises(ReportError) as err, uow(engine) as conn:
            service().publish(conn, bundle("payments_v2_revised"),
                              revision_of=RevisionRef(review_cycle_id="CYCLE-a", expected_previous=1))
    finally:
        event.remove(engine, "before_cursor_execute", concurrent_writer)
    assert moved["done"] and err.value.envelope.code == "VERSION_CONFLICT"
    assert (head(engine), count(engine, "report_versions")) == (1, 1)  # rolled back as a unit


def test_publisher_that_lost_the_version_number_race_gets_a_conflict(engine):
    """A concurrent writer inserted (report_id, 2) first: our insert hits the primary key."""
    from sqlalchemy import event
    publish_v1(engine)
    raced = {"done": False}

    def rival_insert(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("INSERT INTO REPORT_VERSIONS") and not raced["done"]:
            raced["done"] = True
            cursor.execute(statement, parameters)

    event.listen(engine, "before_cursor_execute", rival_insert)
    try:
        with pytest.raises(ReportError) as err, uow(engine) as conn:
            service().publish(conn, bundle("payments_v2_revised"),
                              revision_of=RevisionRef(review_cycle_id="CYCLE-a", expected_previous=1))
    finally:
        event.remove(engine, "before_cursor_execute", rival_insert)
    assert raced["done"] and err.value.envelope.code == "VERSION_CONFLICT"
    assert (head(engine), count(engine, "report_versions")) == (1, 1)


def test_failure_mid_publish_keeps_head_at_v1(engine):
    publish_v1(engine)
    with engine.begin() as conn:  # make the version insert itself fail inside the publish transaction
        conn.exec_driver_sql("CREATE TRIGGER fail_v2 BEFORE INSERT ON report_versions WHEN NEW.version = 2 "
                             "BEGIN SELECT RAISE(ABORT, 'disk full'); END")
    with pytest.raises(ReportError) as err, uow(engine) as conn:
        service().publish(conn, bundle("payments_v2_revised"),
                          revision_of=RevisionRef(review_cycle_id="CYCLE-x", expected_previous=1))
    assert err.value.envelope.code == "STORAGE_FAILURE" and err.value.envelope.retryable is True
    assert (head(engine), count(engine, "report_versions"), count(engine, "report_contents")) == (1, 1, 1)
    with engine.connect() as conn:
        assert service().get(conn, REPORT_ID).record.version == 1


def test_history_returns_all_versions_in_order(engine):
    publish_v1(engine)
    for n, name in enumerate(("payments_v2_revised", "missing_f4"), start=1):
        with uow(engine) as conn:
            service().publish(conn, bundle(name), revision_of=RevisionRef(review_cycle_id=None, expected_previous=n))
    with engine.connect() as conn:
        history = service().history(conn, REPORT_ID)
    assert [(r.version, r.previous_version) for r in history] == [(1, None), (2, 1), (3, 2)]


def test_get_unknown_report_or_version_is_not_found(engine):
    publish_v1(engine)
    with engine.connect() as conn:
        for report_id, version in (("RPT-missing", None), (REPORT_ID, 7)):
            with pytest.raises(ReportError) as err:
                service().get(conn, report_id, version)
            assert err.value.envelope.code == "NOT_FOUND"


def test_tampered_content_is_detected_on_read(engine):
    publish_v1(engine)
    with engine.begin() as conn:
        conn.execute(text("UPDATE report_contents SET body_json = replace(body_json, 'payments', 'PAYMENTS')"))
    with engine.connect() as conn, pytest.raises(ReportError) as err:
        service().get(conn, REPORT_ID)
    assert err.value.envelope.code == "STORAGE_FAILURE"


def test_uncertainty_flags_describe_the_inconclusive_report(engine):
    with uow(engine) as conn:
        record = service().publish(conn, bundle("inconclusive"))
    assert record.uncertainty_flags == ["missing_evidence", "no_validated_remediation", "root_cause_inconclusive"]


def test_catalog_lists_current_version_per_report(engine):
    publish_v1(engine)
    with uow(engine) as conn:
        service().publish(conn, bundle("db_exhaustion"))
        service().publish(conn, bundle("payments_v2_revised"), revision_of=RevisionRef(review_cycle_id=None,
                                                                                        expected_previous=1))
    with engine.connect() as conn:
        catalog = service().catalog(conn)
    db_incident = bundle("db_exhaustion").incident.incident_id
    assert sorted((r.incident_id, r.version) for r in catalog) == sorted([(db_incident, 1), (PAYMENTS, 2)])
