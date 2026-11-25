"""The container setup must keep the service local, keep secrets out of the image and match Settings."""
import re
from pathlib import Path

import pytest
import yaml

from observa_review_service.settings import Settings

SERVICE = Path(__file__).resolve().parents[1]
REPO = SERVICE.parent


@pytest.fixture(scope="module")
def compose():
    return yaml.safe_load((SERVICE / "compose.yaml").read_text(encoding="utf-8"))


def test_compose_runs_the_ui_and_the_worker_from_one_image(compose):
    services = compose["services"]
    assert set(services) == {"review", "review-worker"}
    for name in services:
        build = services[name]["build"]
        assert build["context"] == ".." and build["dockerfile"] == "review_service/docker/Dockerfile"
        assert services[name]["image"] == "observa-review-service:local"
    assert "--loop" in " ".join(services["review-worker"]["command"])
    assert services["review-worker"]["depends_on"]["review"]["condition"] == "service_healthy"
    assert "healthcheck" in services["review"]


def test_ui_is_bound_to_loopback_on_8095_only(compose):
    assert compose["services"]["review"]["ports"] == ["127.0.0.1:8095:8000"]
    assert "ports" not in compose["services"]["review-worker"]  # the worker serves nothing


def test_database_and_memory_live_on_a_volume_and_reviewers_are_read_only(compose):
    assert "review-data" in compose["volumes"]
    for name, service in compose["services"].items():
        env = service["environment"]
        assert env["DATABASE_URL"] == "sqlite:////data/review.sqlite3", name
        assert env["MEMORY_SINK_DIR"] == "/data/memory" and env["REVIEWERS_FILE"] == "/run/config/reviewers.json"
        volumes = service["volumes"]
        assert {"type": "volume", "source": "review-data", "target": "/data"} in volumes
        [reviewers] = [v for v in volumes if v.get("target") == "/run/config/reviewers.json"]
        assert reviewers["type"] == "bind" and reviewers["read_only"] is True
        assert reviewers["bind"]["create_host_path"] is False  # a missing file fails loudly, never a directory
        assert service["env_file"] == [{"path": ".env", "required": True}]


def test_dockerfile_is_pinned_non_root_and_has_no_secrets():
    dockerfile = (SERVICE / "docker" / "Dockerfile").read_text(encoding="utf-8")
    assert dockerfile.startswith("FROM python:3.12-slim")
    assert "requirements.lock" in dockerfile and "USER " in dockerfile
    for name in ("GROQ_API_KEY", "SESSION_SECRET", "SERVICE_TOKEN", "reviewers.json", ".env"):
        assert name not in dockerfile, name
    ignore = (SERVICE / "docker" / "Dockerfile.dockerignore").read_text(encoding="utf-8")
    for pattern in ("**/.env", "**/reviewers.json", "**/data", "**/*.sqlite3"):
        assert pattern in ignore, pattern


def test_env_example_documents_every_setting_without_real_values():
    example = (SERVICE / ".env.example").read_text(encoding="utf-8")
    keys = dict(re.findall(r"^([A-Z_][A-Z0-9_]*)=(.*)$", example, flags=re.MULTILINE))
    assert set(keys) == set(Settings.model_fields)
    for secret in ("GROQ_API_KEY", "SESSION_SECRET", "SERVICE_TOKEN_SHA256"):
        assert keys[secret] == "", secret  # placeholders only
    assert keys["INTERPRETER"] == "fake"


def test_local_secrets_are_git_ignored():
    import shutil
    import subprocess
    if shutil.which("git") is None:
        pytest.skip("git is not available")
    paths = ["review_service/reviewers.json", "review_service/.env", "review_service/data/review.sqlite3",
             "review_service/data/memory/DEC-1.json"]
    result = subprocess.run(["git", "check-ignore", *paths], cwd=REPO, capture_output=True, text=True)
    assert sorted(result.stdout.split()) == sorted(paths)
    kept = subprocess.run(["git", "check-ignore", "review_service/.env.example",
                           "review_service/reviewers.example.json"], cwd=REPO, capture_output=True, text=True)
    assert kept.stdout.strip() == ""  # the examples are committed


def test_migrate_command_creates_the_schema_before_the_ui_starts(tmp_path, capsys):
    from sqlalchemy import inspect

    from observa_review_service.db import make_engine
    from observa_review_service.migrate import main
    url = f"sqlite:///{(tmp_path / 'fresh' / 'review.sqlite3').as_posix()}"  # parent folder does not exist yet
    main(["--database-url", url])
    main(["--database-url", url])  # idempotent: a restart re-runs it
    engine = make_engine(url)
    try:
        assert {"report_versions", "review_cycles", "outbox_events"} <= set(inspect(engine).get_table_names())
    finally:
        engine.dispose()
    assert "schema is up to date" in capsys.readouterr().out


def test_compose_migrates_before_serving(compose):
    command = " ".join(compose["services"]["review"]["command"])
    assert command.index("observa_review_service.migrate") < command.index("uvicorn")
    assert "--host 0.0.0.0 --port 8000" in command


def test_readme_covers_running_tokens_worker_and_tests():
    readme = (SERVICE / "README.md").read_text(encoding="utf-8")
    for must in ("docker compose -f review_service/compose.yaml up", "python -m observa_review_service.seed",
                 "python -m observa_review_service.worker --loop", "reviewers.json", "hashlib.sha256",
                 "--rootdir", ".env.example", "INTERPRETER=groq", "http://127.0.0.1:8095/ui"):
        assert must in readme, must
