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


F6_PYPROJECT = Path(__file__).resolve().parents[2] / "f6_hitl" / "pyproject.toml"
LOCK = Path(__file__).resolve().parents[1] / "requirements.lock"


def pins(requirements):
    return {r.split("==")[0].strip().lower(): r.split("==")[1].strip() for r in requirements if "==" in r}


def test_f6_pins_the_interpreter_stack_exactly():
    f6 = tomllib.loads(F6_PYPROJECT.read_text(encoding="utf-8"))["project"]
    deps = pins(f6["dependencies"])
    assert deps["instructor"] == "1.17.0" and deps["openai"] == "3.3.0"
    assert all("==" in r for r in f6["dependencies"])  # exact pins only (Global Constraints)


def test_ui_forms_dependency_is_declared_and_everything_is_locked():
    assert "python-multipart" in names(project()["dependencies"])  # /ui/login and /decide read forms
    lock = pins(line for line in LOCK.read_text(encoding="utf-8").splitlines() if not line.startswith("#"))
    for name in ("instructor", "openai", "python-multipart", "sqlalchemy", "fastapi"):
        assert name in lock, name
