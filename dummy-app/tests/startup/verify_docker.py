"""Real fresh-volume/restart/failure checks. Build observa-dummy:startup-validation first.

Uses its own project, image tag, volumes and journal, with no published host ports.
Keeps captured evidence and volumes; never touches observa-dummy containers or data.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import uuid

ROOT = Path(__file__).resolve().parents[2]
RUN = "observa-startup-check-" + uuid.uuid4().hex[:8]
OUT = ROOT / "test-results" / RUN
OUT.mkdir(parents=True, exist_ok=False)
COMPOSE = OUT / "compose.json"


def docker(*args, timeout=240, check=True):
    result = subprocess.run(["docker", *args], cwd=ROOT, capture_output=True, text=True,
                            encoding="utf-8", timeout=timeout)
    if check and result.returncode:
        raise RuntimeError(result.stderr[-4000:] + result.stdout[-1000:])
    return result


def compose(*args, file=COMPOSE, **kwargs):
    return docker("compose", "-f", str(file), *args, **kwargs)


def event(name):
    print(name, flush=True)


def sql(service, query):
    return compose("exec", "-T", "postgres", "psql", "-U", "postgres", "-d", service+"_db",
                   "-At", "-v", "ON_ERROR_STOP=1", "-c", query).stdout.strip()


def versions():
    return {s: sql(s, "SELECT version || ':' || checksum || ':' || applied_at::text "
                     "FROM schema_migrations ORDER BY version") for s in ("inventory", "payments", "orders")}


def container_state(service, file=COMPOSE):
    ident = compose("ps", "-a", "-q", service, file=file).stdout.strip()
    if not ident:
        return {"Status": "absent"}
    return json.loads(docker("inspect", ident).stdout)[0]["State"]


def probe(code, service="gateway"):
    return json.loads(compose("exec", "-T", service, "python", "-c", code).stdout)


def main():
    env = dict(os.environ)
    for key in re.findall(r"\$\{(\w+)", (ROOT/"docker-compose.yml").read_text()):
        env.pop(key, None)
    for key in list(env):
        if key.startswith("COMPOSE_"):
            env.pop(key)
    empty = OUT/"empty.env"
    empty.write_text("")
    result = subprocess.run(["docker", "compose", "--env-file", str(empty), "config", "--format", "json"],
                            cwd=ROOT, env=env, capture_output=True, text=True, check=True)
    config = json.loads(result.stdout)
    config["name"] = RUN
    for section in ("networks", "volumes"):
        for key, value in config[section].items():
            value["name"] = RUN+"_"+key
    for service in config["services"].values():
        service.pop("ports", None)
        service["restart"] = "no"
        if "build" in service:
            service.pop("build")
            service["image"] = "observa-dummy:startup-validation"
    journal = OUT/"ground-truth"
    journal.mkdir()
    config["services"]["fault-injector"]["volumes"] = [
        {"type": "bind", "source": str(journal), "target": "/ground-truth"}]
    config["services"]["traffic-generator"]["environment"]["TRAFFIC_RPS"] = "1"
    config["services"]["payments"]["environment"]["BASELINE_DECLINE_RATE"] = "0"
    COMPOSE.write_text(json.dumps(config, indent=2), encoding="utf-8")
    results = {"project": RUN, "image": "observa-dummy:startup-validation", "checks": {}}
    try:
        event("Starting the complete default stack on fresh, isolated volumes.")
        compose("up", "-d", "--wait", "--wait-timeout", "180")
        setup_state = container_state("db-migrate")
        assert setup_state["Status"] == "exited" and setup_state["ExitCode"] == 0
        before = versions()
        assert [len(before[s].splitlines()) for s in ("inventory", "payments", "orders")] == [1, 1, 3]
        (OUT/"fresh-migration.log").write_text(compose("logs", "--no-color", "db-migrate").stdout)
        customer = "startup-"+uuid.uuid4().hex[:12]
        reference = "ATT-"+uuid.uuid4().hex[:4].upper()+"-0001"
        body = {"customer_id": customer, "items": [{"product_id": "sku-005", "quantity": 1}],
                "contact": {"name": "Startup test", "email": "startup@example.com"}}
        code = (
            "import json;from urllib.request import Request,urlopen;"
            f"body={body!r};ref={reference!r};"
            "req=Request('http://127.0.0.1:8000/api/checkout',data=json.dumps(body).encode(),"
            "headers={'Content-Type':'application/json','Idempotency-Key':ref});"
            "r=urlopen(req,timeout=20);print(json.dumps({'status':r.status,'body':json.load(r)}))"
        )
        placed = probe(code)
        assert placed["status"] == 201 and placed["body"]["outcome"] == "confirmed", placed
        results["checkout"] = placed
        results["checks"]["fresh_database_checkout"] = True
        health = probe("import json;from urllib.request import urlopen;"
                       "r=urlopen('http://grafana:3000/api/health',timeout=10);print(json.dumps({'status':r.status,'body':json.load(r)}))",
                       "telemetry-health")
        assert health["status"] == 200 and health["body"]["database"] == "ok"
        results["checks"]["grafana_without_profile"] = True
        event("Fresh startup passed: checkout confirmed, all five migrations present, Grafana running.")
        compose("down")  # Keep the same volumes for the restart check.
        compose("up", "-d", "--wait", "--wait-timeout", "180")
        assert versions() == before, "Migration history changed on repeated startup"
        path = "/api/attempts/"+reference+"?customer_id="+customer
        found = probe("import json;from urllib.request import urlopen;"
                      f"r=urlopen({'http://127.0.0.1:8000'+path!r},timeout=10);print(json.dumps(json.load(r)))")
        assert found == placed["body"], "The saved checkout changed after restart"
        (OUT/"repeated-migration.log").write_text(compose("logs", "--no-color", "db-migrate").stdout)
        results["checks"]["repeat_preserves_data_and_migration_history"] = True
        event("Repeated startup passed: existing order and migration history preserved.")
        compose("down")
        failed = json.loads(json.dumps(config))
        failed["services"]["db-migrate"]["environment"]["INVENTORY_DATABASE_URL"] = (
            "postgresql://inventory_app:deliberately-wrong-test-password@postgres:5432/inventory_db")
        failed_file = OUT/"failed-setup.compose.json"
        failed_file.write_text(json.dumps(failed, indent=2))
        failure = compose("up", "-d", "gateway", file=failed_file, check=False)
        assert failure.returncode != 0, "Startup succeeded despite broken database setup"
        setup_state = container_state("db-migrate", failed_file)
        assert setup_state["Status"] == "exited" and setup_state["ExitCode"] != 0
        for service in ("inventory", "payments", "orders", "gateway"):
            assert container_state(service, failed_file)["Status"] != "running", service
        failure_log = compose("logs", "--no-color", "db-migrate", file=failed_file).stdout
        assert "deliberately-wrong-test-password" not in failure_log
        (OUT/"failed-setup.log").write_text(failure_log)
        results["checks"]["failed_setup_blocks_business_services"] = True
        event("Failure gate passed: failed setup prevented checkout services from starting.")
        results["passed"] = True
    except Exception as exc:
        results["passed"] = False
        results["error"] = str(exc)
        raise
    finally:
        # Preserve the original result even if Docker itself cannot clean up.
        results["test_containers_removed"] = False
        try:
            cleanup = compose("down", check=False)  # Retain volumes and evidence.
            results["test_containers_removed"] = cleanup.returncode == 0
            if cleanup.returncode:
                results["cleanup_error"] = cleanup.stderr[-2000:]
        except Exception as exc:
            results["cleanup_error"] = str(exc)
        (OUT/"result.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
        event("Results: "+str(OUT/"result.json"))


if __name__ == "__main__":
    main()
