import argparse
import json
from pathlib import Path

from . import correlate


def main():
    parser = argparse.ArgumentParser(description="Correlate one saved F2 investigation result.")
    parser.add_argument("investigation", type=Path)
    args = parser.parse_args()
    try:
        report = json.loads(args.investigation.read_text(encoding="utf-8-sig"))
        result = correlate(report)
    except (OSError, ValueError) as exc:
        parser.exit(2, f"Invalid investigation: {exc}\n")
    except RuntimeError as exc:
        parser.exit(1, f"Correlation failed: {exc}\n")
    print(result.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
