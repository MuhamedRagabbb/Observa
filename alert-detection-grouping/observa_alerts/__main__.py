import argparse
import json
import signal
import threading
import time
from pathlib import Path
from .config import settings
from .collect import collect
from .engine import Engine
from .report import export
from .demo import snapshot


def main():
    parser = argparse.ArgumentParser(description="OBSERVA detection and incident handoff")
    parser.add_argument("command", choices=("once", "watch", "demo"))
    parser.add_argument("--config")
    parser.add_argument("--data", default="data")
    parser.add_argument("--reports", default="reports")
    parser.add_argument("--scenario", choices=("payments", "declines", "pool", "deployment"), default="payments")
    args = parser.parse_args()
    config = settings(args.config)
    if args.command == "demo":
        args.data = str(Path(args.data) / ("demo-" + args.scenario))
        args.reports = str(Path(args.reports) / ("demo-" + args.scenario))
    engine = Engine(args.data, config)
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    try:
        if args.command == "demo":
            start = max(1793786400, engine.state["last_poll"] + 100)
            for offset in range(0, 51, 10):
                incidents = engine.tick(snapshot(config, start+offset, args.scenario))
            export(incidents, args.reports)
            print(json.dumps({"origin": "synthetic_demo", "incidents": len(incidents), "reports": args.reports}))
            return
        while not stop.is_set():
            tick = time.monotonic()
            snap = collect(config)
            incidents = engine.tick(snap)
            export(incidents, args.reports)
            Path(args.data, "last-source-status.json").write_text(json.dumps({
                "time": snap["time"], "sources": snap["source_health"]}, indent=2), encoding="utf-8")
            print(json.dumps({"sources": snap["source_health"], "incidents": len(incidents)}), flush=True)
            if args.command == "once":
                break
            stop.wait(max(.1, config["poll_seconds"]-(time.monotonic()-tick)))
    finally:
        engine.close()


if __name__ == "__main__":
    main()
