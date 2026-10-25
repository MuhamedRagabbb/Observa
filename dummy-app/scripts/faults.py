"""Cross-platform, standard-library-only wrapper for the evaluation controller."""

import argparse
import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("action", choices=["activate", "deactivate", "status", "auto-start", "auto-stop"])
parser.add_argument(
    "scenario", nargs="?", choices=["db_connection_exhaustion", "payments_errors", "bad_deployment"]
)
parser.add_argument("--url", default="http://localhost:8090")
parser.add_argument("--duration", type=float, default=120)
parser.add_argument("--parameters", default="{}", help='JSON object, e.g. {"failure_rate":1}')
parser.add_argument("--warmup", type=float, default=120)
parser.add_argument("--recovery", type=float, default=90)
parser.add_argument("--seed", type=int, default=42)
parser.add_argument("--repeat", type=int, default=1)
parser.add_argument("--shuffle", action="store_true")
args = parser.parse_args()
body = {}
if args.action in ("activate", "deactivate"):
    if not args.scenario:
        parser.error("activate/deactivate require a scenario")
    body = {"scenario": args.scenario}
    if args.action == "activate":
        body.update(duration_seconds=args.duration, parameters=json.loads(args.parameters))
elif args.action == "auto-start":
    body = {
        "warmup_seconds": args.warmup,
        "duration_seconds": args.duration,
        "recovery_seconds": args.recovery,
        "seed": args.seed,
        "repeat": args.repeat,
        "shuffle": args.shuffle,
    }
path = "/faults/" + args.action.replace("-", "/")
request = Request(
    args.url + path,
    data=json.dumps(body).encode() if args.action != "status" else None,
    headers={"content-type": "application/json"},
    method="GET" if args.action == "status" else "POST",
)
try:
    with urlopen(request, timeout=10) as response:
        print(json.dumps(json.load(response), indent=2))
except HTTPError as exc:
    print(exc.read().decode())
    raise SystemExit(1) from None
