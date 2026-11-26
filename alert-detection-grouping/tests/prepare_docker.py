"""Create isolated Docker validation manifests without modifying dummy-app."""
import json
import os
from pathlib import Path
import re
import subprocess
import uuid

ROOT = Path(__file__).resolve().parents[1]
DUMMY = ROOT.parent / "dummy-app"


def main():
    run_id = "observa-alert-validation-" + uuid.uuid4().hex[:8]
    output = ROOT / "validation" / run_id
    output.mkdir(parents=True)
    empty = output / "empty.env"
    empty.write_text("", encoding="utf-8")
    env = dict(os.environ)
    for key in re.findall(r"\$\{(\w+)", (DUMMY/"docker-compose.yml").read_text()):
        env.pop(key, None)
    env.pop("COMPOSE_PROJECT_NAME", None)
    base = json.loads(subprocess.check_output([
        "docker", "compose", "--env-file", str(empty), "-f", str(DUMMY/"docker-compose.yml"),
        "config", "--format", "json"], env=env, text=True))
    base["name"] = run_id
    for key, value in base["networks"].items():
        value["name"] = run_id + "_" + key
    for key, value in base["volumes"].items():
        value["name"] = run_id + "_" + key
    for key, value in base["services"].items():
        value.pop("ports", None)
        value["restart"] = "no"
        if "build" in value:
            value.pop("build")
            value["image"] = "observa-dummy:alert-validation"
    base["services"]["traffic-generator"]["profiles"] = ["traffic"]
    base["services"]["traffic-generator"]["environment"]["TRAFFIC_FLOW"] = "checkout"
    base["services"]["traffic-generator"]["environment"]["TRAFFIC_RPS"] = "1"
    ground_truth = output/"evaluation-ground-truth"
    ground_truth.mkdir()
    base["services"]["fault-injector"]["volumes"] = [
        {"type":"bind", "source":str(ground_truth), "target":"/ground-truth"}]
    detector_project = run_id + "-detector"
    detector = json.loads(subprocess.check_output([
        "docker","compose","--env-file",str(empty),"-f",str(ROOT/"docker-compose.yml"),
        "config","--format","json"],env=env,text=True))
    detector["name"] = detector_project
    detector["networks"]["telemetry"]["name"] = run_id + "_observability"
    for key,value in detector["volumes"].items():
        value["name"] = detector_project + "_" + key
    detector["services"]["alerts"].pop("build",None)
    detector["services"]["alerts"].update(image="observa-alerts:validation", restart="no")
    # Override the normal host reports bind: an isolated test must never write live reports.
    detector["volumes"]["alert-reports"] = {"name": detector_project + "_alert-reports"}
    detector["services"]["alerts"]["volumes"] = [
        {"type": "volume", "source": "alert-reports", "target": "/app/reports"}
        if mount["target"] == "/app/reports" else mount
        for mount in detector["services"]["alerts"]["volumes"]]
    base["volumes"]["detector-reports"] = {
        "external": True, "name": detector_project + "_alert-reports"}
    # Probe is evaluation infrastructure, never imported or accessed by the detector.
    base["services"]["probe"] = {
        "image":"observa-alerts:validation", "profiles":["probe"], "user":"0:0",
        "command":["python","/validation/docker_probe.py"],
        "networks":["business","control","observability"],
        "environment":{"PROMETHEUS_URL":"http://prometheus:9090", "LOKI_URL":"http://loki:3100",
                       "JAEGER_URL":"http://jaeger:16686"},
        "volumes":[{"type":"bind","source":str(ROOT/"tests"/"docker_probe.py"),
                    "target":"/validation/docker_probe.py","read_only":True},
                   {"type":"bind","source":str(output),"target":"/results"},
                   {"type":"volume","source":"detector-reports","target":"/detector-reports","read_only":True}],
    }
    (output/"dummy.compose.json").write_text(json.dumps(base,indent=2),encoding="utf-8")
    (output/"detector.compose.json").write_text(json.dumps(detector,indent=2),encoding="utf-8")
    manifest={"run_id":run_id,"output":str(output),"dummy":str(output/"dummy.compose.json"),
              "detector":str(output/"detector.compose.json")}
    (ROOT/"validation"/"current-run.json").write_text(json.dumps(manifest,indent=2),encoding="utf-8")
    print(json.dumps(manifest))


if __name__=="__main__":
    main()
