"""Write the Gateway and controller OpenAPI documents that the web app's types are generated from.

Runs offline: it builds each app in-process and never opens a database, a network connection
or the ground-truth journal. Output is sorted, indented JSON, so it is byte-for-byte stable.

    python -m scripts.export_openapi            # write web/openapi/{gateway,controller}.json
    python -m scripts.export_openapi --check    # exit 1 if a committed snapshot is stale

After writing, regenerate the TypeScript types with `npm run gen:api` in web/.
"""

import argparse
import json
import sys
import tempfile
from pathlib import Path

from controllers.fault_injector.app import create_app as controller_app
from services.gateway.app import create_app as gateway_app
from services.shared.config import Settings

DEFAULT_OUT = Path(__file__).resolve().parents[1] / "web" / "openapi"


def schemas():
    """The OpenAPI document of each origin the web app talks to, keyed by snapshot name."""
    gateway = gateway_app(Settings(otlp_endpoint="")).openapi()
    with tempfile.TemporaryDirectory() as scratch:
        # The journal path is only opened at startup, which never runs here.
        controller = controller_app(path=Path(scratch) / "unused.jsonl").openapi()
    return {"gateway": gateway, "controller": controller}


def render(schema):
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail instead of writing when stale")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="snapshot folder")
    args = parser.parse_args(argv)

    stale = []
    for name, schema in schemas().items():
        target = args.out / f"{name}.json"
        text = render(schema)
        current = target.read_text(encoding="utf-8") if target.exists() else None
        if current == text:
            continue
        if args.check:
            stale.append(target.name)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8", newline="\n")
            print(f"wrote {target}")
    if stale:
        print(f"stale OpenAPI snapshots: {', '.join(stale)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
