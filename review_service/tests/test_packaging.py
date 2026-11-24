"""The composition root must install and run with F7 alone; F6 is an optional extra."""
import tomllib
from pathlib import Path

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def project():
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]


def names(requirements):
    return {r.split("==")[0].split(">=")[0].split("[")[0].strip().lower() for r in requirements}


def test_f6_is_not_a_hard_dependency():
    assert "observa-hitl" not in names(project()["dependencies"])
    assert "observa-reports" in names(project()["dependencies"])


def test_f6_is_available_as_the_f6_extra():
    assert "observa-hitl" in names(project()["optional-dependencies"]["f6"])
