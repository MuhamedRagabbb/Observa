"""A deterministic keyword interpreter: used by every test and by `INTERPRETER=fake`.

Per sentence it collects the report targets the reviewer names (IDs first, then service and
signal names outside those IDs) and picks an action from keywords. A sentence that names
nothing is ignored; feedback that names nothing at all is inconclusive.
"""
import re

from observa_reports.content import ReportContent

from .schema import SIGNALS, InterpretationResult, InterpretedFeedback

_SENTENCES = re.compile(r"(?<=[.!?])\s+|\n+")
_ACTIONS = (
    ("correct_report_text", ("typo", "wording", "rephrase", "spelling", "grammar")),
    ("reassess_recommendation", ("recommend", "remediation", "rollback", "roll back", "restart")),
    ("reassess_root_cause", ("root cause", "cause")),
)


def _action(sentence: str) -> str:
    lowered = sentence.lower()
    for action, words in _ACTIONS:
        if any(w in lowered for w in words):
            return action
    return "reinvestigate"


def _mentions(sentence: str, report: ReportContent, targets: set[str]) -> list[str]:
    found, rest = [], sentence
    ids = sorted((t for t in targets if t not in SIGNALS and re.search(r"[-:]", t) and t.upper() != t.lower()),
                 key=len, reverse=True)
    for t in ids:  # longest first, then cut it out so its parts ("logs") are not matched again
        if t in rest:
            found.append(t)
            rest = rest.replace(t, " ")
    for t in sorted(targets):
        if t in found:
            continue
        if re.search(rf"(?<![\w-]){re.escape(t)}(?![\w-])", rest, flags=re.IGNORECASE):
            found.append(t)
    if not found and "cause" in sentence.lower() and report.root_cause.leading:
        found.append(report.root_cause.leading.candidate_id)
    return found


class FakeInterpreter:
    name = "fake"

    def interpret(self, feedback: str, report: ReportContent, allowed_targets: set[str]) -> InterpretationResult:
        changes, seen = [], set()
        for sentence in (s.strip() for s in _SENTENCES.split(feedback or "")):
            if not sentence:
                continue
            action = _action(sentence)
            for target in _mentions(sentence, report, allowed_targets):
                if (target, action) not in seen:
                    seen.add((target, action))
                    changes.append({"target": target, "action": action, "reason": sentence[:1000]})
        if changes:
            summary = "Reviewer asks to " + "; ".join(f"{c['action'].replace('_', ' ')} {c['target']}"
                                                      for c in changes) + "."
            data = {"summary_of_human_intent": summary, "requested_changes": changes, "agent_status": "completed"}
        else:
            data = {"summary_of_human_intent": None, "requested_changes": [], "agent_status": "inconclusive"}
        output = InterpretedFeedback.model_validate(data, context={"allowed_targets": allowed_targets})
        return InterpretationResult(output=output, attempts=1, raw=None, interpreter=self.name)
