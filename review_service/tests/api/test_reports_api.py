"""Task 3.1: the read API over F7 report versions, with the F6 review-status projection."""
from review_helpers import bundle

CONTRACT_REPORT_VERSION = {
    "report_id", "incident_id", "version", "previous_version", "content_ref", "content_digest",
    "source_investigation_run_id", "created_at", "review_status", "review_cycle_id", "evidence_ids",
    "uncertainty_flags",
}


def test_get_current_and_exact_version(client, publish):
    v1 = publish("payments_v1")
    publish("payments_v2_revised", expected_previous=1, review_cycle_id="CYCLE-1")
    current = client.get(f"/api/v1/reports/{v1.report_id}")
    exact = client.get(f"/api/v1/reports/{v1.report_id}/versions/1")
    assert current.status_code == exact.status_code == 200
    assert current.json()["report_version"]["version"] == 2
    assert current.json()["report_version"]["previous_version"] == 1
    assert exact.json()["report_version"]["content_digest"] == v1.content_digest
    assert exact.json()["content"]["overview"]["end_time"] is None
    assert current.json()["content"]["overview"]["end_time"] == "2026-11-23T14:35:05.058730Z"


def test_report_version_contract_fields_exact(client, publish):
    record = publish("payments_v1")
    body = client.get(f"/api/v1/reports/{record.report_id}").json()["report_version"]
    assert set(body) == CONTRACT_REPORT_VERSION
    assert body["created_at"] == "2026-11-25T12:00:00Z"
    assert body["content_ref"] == f"sha256:{record.content_digest}"


def test_review_status_defaults_to_pending_without_f6(client, publish):
    record = publish("payments_v1")
    assert client.get(f"/api/v1/reports/{record.report_id}").json()["report_version"]["review_status"] == \
        "pending_review"


def test_versions_lists_history_in_order(client, publish):
    v1 = publish("payments_v1")
    publish("payments_v2_revised", expected_previous=1)
    versions = client.get(f"/api/v1/reports/{v1.report_id}/versions").json()
    assert [(v["version"], v["previous_version"]) for v in versions] == [(1, None), (2, 1)]


def test_list_reports_returns_each_current_version(client, publish):
    publish("payments_v1")
    publish("db_exhaustion")
    listing = client.get("/api/v1/reports").json()
    assert {item["incident_id"] for item in listing} == {bundle("payments_v1").incident.incident_id,
                                                         bundle("db_exhaustion").incident.incident_id}
    assert all(set(item) == CONTRACT_REPORT_VERSION | {"title"} for item in listing)


def test_markdown_export_endpoint(client, publish):
    record = publish("payments_v1")
    response = client.get(f"/api/v1/reports/{record.report_id}/versions/1/report.md")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    assert response.text.startswith("# Incident report · INC-6558d5b7036a44934e9e")


def test_unknown_report_404_envelope(client, publish):
    publish("payments_v1")
    for path in ("/api/v1/reports/RPT-missing", "/api/v1/reports/RPT-missing/versions/1"):
        response = client.get(path)
        assert response.status_code == 404
        assert set(response.json()) == {"code", "message", "retryable", "operation_id", "details"}
        assert response.json()["code"] == "NOT_FOUND"
