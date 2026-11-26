"""Run after prepare_docker.py and isolated stacks/migrations are ready."""
import json
from pathlib import Path
import subprocess
import time
import argparse

ROOT=Path(__file__).resolve().parents[1]
RUN=json.loads((ROOT/"validation"/"current-run.json").read_text(encoding="utf-8"))
OUTPUT=Path(RUN["output"])
PROBE=RUN["run_id"]+"-probe"
RESULTS=[]


def docker(*args):
    result=subprocess.run(["docker",*args],capture_output=True,text=True,timeout=90)
    if result.returncode:
        raise RuntimeError(result.stderr+"\n"+result.stdout)
    return result.stdout.strip()


def event(**data):
    data["time"]=time.time()
    with (OUTPUT/"execution.jsonl").open("a",encoding="utf-8") as f:
        f.write(json.dumps(data)+"\n")
    print(json.dumps(data),flush=True)


def checkpoint():
    (OUTPUT/"results.json").write_text(json.dumps(RESULTS,indent=2),encoding="utf-8")


def probe(*args):
    output=docker("exec",PROBE,"python","/validation/docker_probe.py",*args)
    return json.loads(output)


def wait(label, predicate, timeout=240):
    deadline=time.monotonic()+timeout
    while True:
        s=probe("snapshot","--label",label)
        active=[i for i in s["incidents"] if i["status"]=="open"]
        event(phase=label, sources=s["source_health"],
              active=[{"id":i["id"],"rules":i["rules"],"services":i["services"],"gaps":i["gaps"]} for i in active])
        if predicate(s):
            return s
        if time.monotonic()>deadline:
            raise AssertionError("Timed out: "+label)
        time.sleep(15)


def healthy(s):
    return all(s["source_health"].get(k)=="ok" for k in ("prometheus","loki")) and not any(i["status"]=="open" for i in s["incidents"])


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--resume-from",choices=("payments-503","payments-402","pool","deployment"))
    args=parser.parse_args()
    active_scenario=None
    if args.resume_from:
        if (OUTPUT/"results.json").exists():
            RESULTS.extend(json.loads((OUTPUT/"results.json").read_text()))
        elif args.resume_from=="payments-402":
            prior=json.loads((OUTPUT/"payments-503-during"/"summary.json").read_text())
            passed=[json.loads(x) for x in (OUTPUT/"execution.jsonl").read_text().splitlines()
                    if json.loads(x).get("case")=="payments-503" and json.loads(x).get("result")=="passed"]
            assert passed, "No saved 503 success"
            RESULTS.append({"case":"payments-503","passed":True,
                            "detection_delay_seconds":passed[-1]["detection_delay_seconds"],
                            "incidents":[i for i in prior["incidents"] if i["status"]=="open"],
                            "restored_from":"execution.jsonl and payments-503-during/summary.json"})
        docker("inspect",PROBE)
    else:
        docker("compose","-f",RUN["dummy"],"--profile","probe","run","-d","--name",PROBE,"probe",
               "python","-c","import time; time.sleep(7200)")
    try:
        wait("healthy-baseline",healthy,300)
        time.sleep(20)
        stable=probe("snapshot","--label","baseline-confirmed")
        assert healthy(stable), "Healthy baseline not sustained"
        RESULTS.append({"case":"baseline","passed":True})
        checkpoint()
        started=not args.resume_from
        for scenario,label,rule,code in (
            ("payments_errors","payments-503","payment_failures",503),
            ("payments_errors","payments-402","payment_failures",402),
            ("db_connection_exhaustion","pool","pool_timeouts",503),
            ("bad_deployment","deployment","persistence_errors",503)):
            if label==args.resume_from:
                started=True
            if not started:
                continue
            if args.resume_from:
                label += "-resumed"
            before=probe("snapshot","--label",label+"-before")
            old={i["id"] for i in before["incidents"]}
            activation=probe("activate","--scenario",scenario,"--status-code",str(code),"--label",label)
            active_scenario=scenario
            detected=wait(label+"-detected",lambda s:any(i["id"] not in old and i["status"]=="open" and rule in i["rules"] for i in s["incidents"]),150)
            # Keep the fault present long enough for the whole propagation chain to be observed.
            time.sleep(25)
            during=probe("snapshot","--label",label+"-during")
            incidents=[i for i in during["incidents"] if i["id"] not in old and i["status"]=="open"]
            delay=min(
                __import__("datetime").datetime.fromisoformat(i["created_at"].replace("Z","+00:00")).timestamp()
                for i in incidents if rule in i["rules"])-activation["started_at"]
            result={"case":label,"passed":True,"detection_delay_seconds":round(delay,2),
                    "incidents":incidents,"expected_rule":rule}
            # Individual rule detection is necessary; causal grouping is separately reported.
            result["cross_service_grouping"]=any(len(i["services"])>=2 and i["relationship_count"]>0 for i in incidents)
            if label.startswith("payments-503"):
                assert any(set(i["services"])=={"payments-service","orders-service","api-gateway"} for i in incidents), "Payment cascade was split into separate incidents"
                assert any(i["retained_logs"] and i["retained_traces"] for i in incidents), "Missing live evidence"
            RESULTS.append(result)
            checkpoint()
            probe("deactivate","--scenario",scenario)
            active_scenario=None
            wait(label+"-recovered",healthy,300)
            event(case=label,result="passed",detection_delay_seconds=result["detection_delay_seconds"])
            result["recovery_confirmed"]=True
            checkpoint()
        # Source outage must create explicit telemetry incidents; restart must clear them.
        docker("compose","-f",RUN["dummy"],"stop","prometheus")
        try:
            wait("prometheus-outage",lambda s:any(i["status"]=="open" and "observability:prometheus" in i["services"] for i in s["incidents"]),120)
        finally:
            docker("compose","-f",RUN["dummy"],"start","prometheus")
        wait("prometheus-restored",healthy,240)
        RESULTS.append({"case":"prometheus-outage-recovery","passed":True})
        checkpoint()
        before=probe("snapshot","--label","before-detector-restart")
        docker("compose","-f",RUN["detector"],"restart","alerts")
        time.sleep(25)
        after=probe("snapshot","--label","after-detector-restart")
        assert {i["id"] for i in before["incidents"]}=={i["id"] for i in after["incidents"]}, "Restart duplicated or lost incidents"
        RESULTS.append({"case":"detector-restart","passed":True})
        checkpoint()
    except Exception as exc:
        RESULTS.append({"case":"execution","passed":False,"error":str(exc)})
        event(error=str(exc))
        raise
    finally:
        if active_scenario:
            try:
                probe("deactivate","--scenario",active_scenario)
            except Exception as exc:
                event(cleanup_error=str(exc))
        (OUTPUT/"results.json").write_text(json.dumps(RESULTS,indent=2),encoding="utf-8")
        docker("rm","-f",PROBE)


if __name__=="__main__":
    main()
