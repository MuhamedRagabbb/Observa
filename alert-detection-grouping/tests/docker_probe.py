"""Evaluation only. Talks to the test controller; detector never sees ground truth."""
import argparse
import json
from pathlib import Path
import time
import sys
sys.path.insert(0, "/app")
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from observa_alerts.collect import collect
from observa_alerts.config import settings
from observa_alerts.archive import restore_snapshot

RESULTS=Path("/results")


def request(path, body=None):
    data=None if body is None else json.dumps(body).encode()
    req=Request("http://fault-injector:8090"+path,data=data,
                headers={"Content-Type":"application/json", "Host":"localhost:8090"},
                method="GET" if body is None else "POST")
    try:
        with urlopen(req,timeout=15) as response:
            return json.load(response)
    except HTTPError as exc:
        raise RuntimeError(f"Controller {exc.code}: {exc.read().decode()}") from None


def reports():
    root = Path("/detector-reports")
    compact = [json.loads(p.read_text()) for p in root.glob("INC-*/incident.json")]
    if compact:
        return [restore_snapshot(root, i["id"], i["snapshot_id"]) for i in compact]
    return [json.loads(p.read_text()) for p in root.glob("INC-*.json")]


def save(label):
    sample=collect(settings())
    folder=RESULTS/label
    folder.mkdir(exist_ok=True)
    (folder/"sample.json").write_text(json.dumps(sample,indent=2),encoding="utf-8")
    found=reports()
    for item in found:
        # Audit captures intentionally reconstruct full v1 evidence; agent inputs remain compact.
        (folder/(item["id"]+".json")).write_text(json.dumps(item,indent=2),encoding="utf-8")
        source=Path("/detector-reports")/item["id"]/"summary.md"
        if source.exists():
            (folder/(item["id"]+".md")).write_bytes(source.read_bytes())
    status={"label":label, "time":time.time(), "source_health":sample["source_health"],
            "metrics":sample["metrics"],"log_count":len(sample["logs"]),"trace_count":len(sample["traces"]),
            "incidents":[{"id":i["id"],"status":i["status"],"services":i["affected_services"],
                          "anchor":i["anchor"],"relationship_count":len(i["relationships"]),
                          "retained_logs":len(i["evidence"]["logs"]),"retained_traces":len(i["evidence"]["traces"]),
                          "rules":[a["rule"] for a in i["alerts"].values()],"gaps":i["data_gaps"],
                          "created_at":i["created_at"],"resolved_at":i.get("resolved_at")}
                         for i in found]}
    (folder/"summary.json").write_text(json.dumps(status,indent=2),encoding="utf-8")
    print(json.dumps(status),flush=True)
    return status


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("command",choices=("snapshot","activate","deactivate","status"))
    parser.add_argument("--label",default="diagnostic")
    parser.add_argument("--scenario")
    parser.add_argument("--status-code",type=int,default=503)
    args=parser.parse_args()
    if args.command=="snapshot":
        save(args.label)
    elif args.command=="status":
        print(json.dumps(request("/faults/status")))
    elif args.command=="deactivate":
        print(json.dumps(request("/faults/deactivate",{"scenario":args.scenario})))
    else:
        parameters={"payments_errors":{"failure_rate":1,"status_code":args.status_code},
                    "db_connection_exhaustion":{"hold_seconds":8},
                    "bad_deployment":{"delay_seconds":1.5}}[args.scenario]
        response=request("/faults/activate",{"scenario":args.scenario,"duration_seconds":180,
                                           "parameters":parameters})
        record={"started_at":time.time(),"scenario":args.scenario,"parameters":parameters,"response":response}
        (RESULTS/(args.label+"-activation.json")).write_text(json.dumps(record,indent=2),encoding="utf-8")
        print(json.dumps(record),flush=True)


if __name__=="__main__":
    main()
