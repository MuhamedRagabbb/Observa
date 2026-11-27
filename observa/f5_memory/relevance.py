"""Configurable, explainable keyword relevance. Replace by passing another scorer.

A scorer needs one method: ``score(query, services, record, min_matched_terms=None)``
returning a relevance-basis dict, or None when the record does not match.
"""

import re
from dataclasses import dataclass, field

GENERAL_STOP_WORDS = "a an and are as at be been by for from had has have in is it no not of on or that the this to was were with"
# Words that describe almost every incident, so they say nothing about similarity.
INCIDENT_STOP_WORDS = "error failure fail failed failing incident request response service high"
# Fixed wording in every F1 incident title ("Observed ... symptoms in ...") and in its generic
# alert titles ("HTTP 5xx ratio", "HTTP p95 duration"). Checked against F1's real examples.
F1_STOP_WORDS = "observed symptom http 5xx ratio p95 duration"
DEFAULT_STOP_WORDS = frozenset(f"{GENERAL_STOP_WORDS} {INCIDENT_STOP_WORDS} {F1_STOP_WORDS}".split())
# F7's executive summary is generated from the other fields plus fixed wording, so it is
# not searched: its boilerplate would match almost any query.
DEFAULT_FIELD_WEIGHTS = {"root_cause": 2, "findings": 2, "title": 1, "recommendations": 1}
# F1 lists data sources such as "observability:prometheus" among affected services.
# They are not application services, so they never count as a shared service.
PSEUDO_SERVICE_PREFIXES = ("observability:",)


def _stem(token):
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def tokenize(value, stop_words=DEFAULT_STOP_WORDS):
    """Split telemetry names too: db_pool_timeout, db.pool.acquire and PoolTimeoutError."""
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", value)
    stems = {_stem(token) for token in re.findall(r"[^\W_]+", spaced.casefold()) if token not in stop_words}
    return stems - stop_words


def service_name(name):
    """Compare ``orders`` and ``orders-service`` as the same service."""
    return name.casefold().removesuffix("-service")


def _service_name_stripper(names):
    names = sorted({n for n in names if n.strip()}, key=len, reverse=True)
    if not names:
        return lambda value: value
    pattern = re.compile("|".join(rf"(?<![\w-]){re.escape(n)}(?![\w-])" for n in names), re.IGNORECASE)
    return lambda value: pattern.sub(" ", value)


def application_services(names):
    return {service_name(n) for n in names if not n.casefold().startswith(PSEUDO_SERVICE_PREFIXES)}


def _searchable_fields(record):
    root_cause = record["final_root_cause"]
    return {
        "title": [record["incident_context"]["title"]],
        "findings": [part for f in record["findings"] for part in (f["claim"], f["indicator"] or "")],
        "root_cause": [root_cause["description"] if root_cause else "", record["verified_root_cause"] or ""],
        "recommendations": [part for rec in record["accepted_recommendations"]
                            for part in (rec["action"], rec["rationale"])],
    }


@dataclass(frozen=True)
class KeywordScorer:
    service_weight: int = 3
    field_weights: dict = field(default_factory=lambda: dict(DEFAULT_FIELD_WEIGHTS))
    min_matched_terms: int = 1  # 0 lets a shared service match without any shared term
    stop_words: frozenset = DEFAULT_STOP_WORDS
    method: str = "weighted_keyword_overlap_v3"

    def score(self, query, services, record, min_matched_terms=None):
        required = self.min_matched_terms if min_matched_terms is None else min_matched_terms
        current = application_services(services)
        historical = application_services(record["incident_context"]["affected_services"])
        # Service names already count through matched_services. Written out in claims
        # ("payments-service logs show ...") they would count a second time, so the full names
        # are removed from the text before matching; words such as "payment" elsewhere still count.
        strip = _service_name_stripper([*services, *record["incident_context"]["affected_services"]])
        query_terms = tokenize(strip(query), self.stop_words)
        matched_in, term_scores = {}, {}
        for name, parts in _searchable_fields(record).items():
            terms = query_terms & tokenize(strip(" ".join(parts)), self.stop_words)
            if terms:
                matched_in[name] = sorted(terms)
            weight = self.field_weights.get(name, 1)
            for term in terms:
                term_scores[term] = max(term_scores.get(term, 0), weight)
        matched_services = sorted(current & historical)
        score = self.service_weight * len(matched_services) + sum(term_scores.values())
        if score == 0 or len(term_scores) < required:
            return None
        return {
            "matched_services": matched_services,
            "matched_terms": sorted(term_scores),
            "matched_in": matched_in,
            "historical_only_services": sorted(historical - current),
            "current_only_services": sorted(current - historical),
            "ranking_score": score,
            "method": self.method,
            "caveat": "Historical similarity is context, not confirmation of the current root cause.",
        }
