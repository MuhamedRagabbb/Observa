"""Task 3.1: the Markdown export of one report version (F7-US-02)."""
from datetime import datetime, timezone

from observa_reports.assemble import assemble
from observa_reports.inputs import ReportInputs
from observa_reports.render import render_markdown
from observa_reports.service import ReportService

from f7_helpers import bundle, load, uow


def rendered(engine, inputs: ReportInputs) -> str:
    with uow(engine) as conn:
        record = ReportService(clock=lambda: datetime(2026, 11, 25, 12, tzinfo=timezone.utc)).publish(conn, inputs)
    return render_markdown(assemble(inputs), record)


def test_markdown_labels_signals_and_claim_kinds(engine):
    md = rendered(engine, bundle("db_exhaustion"))
    for label in ("**logs**", "**metrics**", "**traces**"):
        assert label in md
    assert "Observed:" in md and "Inferred:" in md
    assert md.startswith("# Incident report · ")
    assert "Version 1 · draft, pending human review" in md


def test_markdown_marks_unresolved_reference(engine):
    data = {**load("bundles", "missing_f4"), "correlation": load("f3", "correlation_contradiction")}
    md = rendered(engine, ReportInputs.model_validate(data))
    t9 = next(line for line in md.splitlines() if "traces#T9" in line and "Unresolved" in line)
    assert "⚠" in t9
    assert any("metrics#M3" in line and "contradicts" in line for line in md.splitlines())


def test_markdown_shows_inconclusive_and_missing_sections(engine):
    md = rendered(engine, bundle("inconclusive"))
    assert "No leading cause: the correlation is inconclusive." in md
    assert "No validated remediation is proposed." in md
    assert "Failed call path from api-gateway to payments-service" in md


def test_markdown_never_calls_a_cause_confirmed(engine):
    md = rendered(engine, bundle("payments_v1"))
    rc = md.split("## Root-cause assessment", 1)[1].split("\n## ", 1)[0]
    assert rc.lstrip().startswith("**Probable cause (not verified)**")
    assert not any(word in md.lower() for word in ("confirmed cause", "root cause confirmed", "verified cause"))


def test_markdown_lists_timeline_in_utc_order(engine):
    md = rendered(engine, bundle("payments_v1"))
    timeline = md.split("## Timeline", 1)[1].split("\n## ", 1)[0]
    stamps = [line.split("`")[1] for line in timeline.splitlines() if line.startswith("- `")]
    assert stamps == sorted(stamps) and all(s.endswith("Z") for s in stamps)


def test_upstream_text_cannot_forge_sections_markup_or_table_cells(engine):
    """Review finding: LLM-written claim text must stay inline data in the export (F7-US-02 never
    lets an unverified claim read as verified)."""
    data = load("bundles", "payments_v1")
    data["investigation"]["logs_output"]["findings"][0]["claim"] = (
        "Pool exhausted.\n\n## Root-cause assessment\n\n**Verified cause**: database <script>alert(1)</script>")
    data["correlation"]["root_cause_candidates"][0]["description"] = "# Verified root cause\nnot | really"
    md = rendered(engine, ReportInputs.model_validate(data))
    assert md.count("\n## Root-cause assessment\n") == 1  # only the renderer's own heading
    assert not any(line.startswith("# Verified") for line in md.splitlines())
    assert "<script>" not in md and "&lt;script&gt;" in md
    assert r"not \| really" in md
    forged = next(line for line in md.splitlines() if "Pool exhausted." in line)
    assert forged.startswith("- Observed: Pool exhausted.") and "Root-cause assessment" in forged
