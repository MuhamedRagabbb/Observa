"""Task 3.2: the reviewer dashboard (Jinja2 + Tailwind), read-only until F6 provides the review gate."""
import re

from observa_reports.inputs import ReportInputs

from review_helpers import load


def page(client, record, version=None):
    path = f"/ui/reports/{record.report_id}" + (f"/v/{version}" if version else "")
    response = client.get(path)
    assert response.status_code == 200, response.text[:300]
    assert response.headers["content-type"].startswith("text/html")
    return response.text


def test_page_shows_version_draft_banner_and_status(client, publish):
    html = page(client, publish("payments_v1"))
    assert 'data-testid="draft-banner"' in html and "pending human review" in html
    assert re.search(r'data-testid="version-label"[^>]*>\s*v1\s*<', html)
    pill = re.search(r'<span[^>]*data-testid="status-pill"[^>]*>', html).group(0)
    assert "bg-amber-500/10" in pill and "text-amber-300" in pill
    assert "Pending review" in html
    assert "Observed payment symptoms in payments-service" in html


def test_inconclusive_banner_rendered(client, publish):
    html = page(client, publish("inconclusive"))
    banner = re.search(r'<div[^>]*data-testid="inconclusive-banner"[^>]*>', html).group(0)
    assert "amber" in banner
    assert "Inconclusive: no leading cause" in html


def test_contradicting_evidence_chip_rendered(client, publish):
    data = {**load("bundles", "missing_f4"), "correlation": load("f3", "correlation_contradiction")}
    html = page(client, publish(ReportInputs.model_validate(data)))
    contradicts = re.search(r'<span[^>]*data-relation="contradicts"[^>]*>', html).group(0)
    assert "bg-rose-600" in contradicts
    unresolved = re.search(r'<span[^>]*data-reference="unresolved"[^>]*>', html).group(0)
    assert "border-dashed" in unresolved and "border-amber-500" in unresolved
    supports = re.search(r'<span[^>]*data-relation="supports"[^>]*data-reference="valid"[^>]*>', html).group(0)
    assert "bg-emerald-600" in supports
    context = re.search(r'<span[^>]*data-relation="context_only"[^>]*>', html).group(0)
    assert "border-slate-600" in context


def test_claim_html_is_escaped(client, publish):
    data = load("bundles", "payments_v1")
    data["investigation"]["logs_output"]["findings"][0]["claim"] = '<script>alert("x")</script> **bold**'
    data["correlation"]["root_cause_candidates"][0]["description"] = '<img src=x onerror=alert(1)> cause'
    html = page(client, publish(ReportInputs.model_validate(data)))
    assert '<script>alert("x")</script>' not in html and "<img src=x" not in html
    assert "&lt;script&gt;" in html and "&lt;img src=x onerror=alert(1)&gt;" in html


def test_action_bar_hidden_for_superseded_version(client, publish):
    v1 = publish("payments_v1")
    publish("payments_v2_revised", expected_previous=1)
    old, current = page(client, v1, 1), page(client, v1)
    assert 'data-testid="action-bar"' not in old and "superseded by v2" in old
    assert 'data-testid="action-bar"' in current


def test_review_actions_disabled_without_f6(client, publish):
    html = page(client, publish("payments_v1"))
    approve = re.search(r'<button[^>]*data-testid="approve-button"[^>]*>', html).group(0)
    change = re.search(r'<button[^>]*data-testid="request-changes-button"[^>]*>', html).group(0)
    assert "disabled" in approve and "disabled" in change
    assert "bg-emerald-600" in approve and "border-amber-500" in change
    assert "Review actions are enabled when F6 is installed" in html


def test_design_tokens_follow_the_sre_dashboard_spec(client, publish):
    html = page(client, publish("db_exhaustion"))
    assert re.search(r'<body[^>]*class="[^"]*bg-slate-950', html)
    assert 'src="https://cdn.tailwindcss.com/3.4.17?plugins=typography"' in html
    assert "bg-slate-900" in html and "border-slate-800" in html and "rounded-xl" in html
    badges = {m.group(1): m.group(0) for m in re.finditer(r'<span[^>]*data-signal="(logs|metrics|traces)"[^>]*>', html)}
    assert "sky" in badges["logs"] and "violet" in badges["metrics"] and "teal" in badges["traces"]
    assert "JetBrains+Mono" in html and "Inter" in html


def test_timeline_is_chronological_utc(client, publish):
    html = page(client, publish("payments_v2_revised"))
    stamps = re.findall(r'data-testid="timeline-time"[^>]*>([^<]+)<', html)
    assert stamps == sorted(stamps) and all(s.strip().endswith("Z") for s in stamps)


def test_index_lists_reports_with_status(client, publish):
    publish("payments_v1")
    publish("bad_deployment")
    html = client.get("/ui").text
    assert html.count('data-testid="report-card"') == 2
    assert "Observed regression symptoms in orders-service" in html


def test_unknown_report_page_is_404_html(client, publish):
    publish("payments_v1")
    response = client.get("/ui/reports/RPT-missing")
    assert response.status_code == 404 and "text/html" in response.headers["content-type"]
    assert "does not exist" in response.text


def test_root_redirects_to_dashboard(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code in (302, 307) and response.headers["location"] == "/ui"
