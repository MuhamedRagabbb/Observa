import argparse
import json
from pathlib import Path
from tempfile import NamedTemporaryFile

from . import correlate
from .reporting.report import render_markdown
from .agent.state import CorrelationReport


def main():
    parser = argparse.ArgumentParser(description="Correlate one saved F2 investigation result.")
    parser.add_argument("investigation", type=Path)
    parser.add_argument("--report-out", type=Path, metavar="FILE",
                        help="Save the standalone report as JSON (.json) or Markdown (.md).")
    parser.add_argument("--from-report", action="store_true",
                        help="Render an existing standalone F3 JSON report without calling Groq.")
    args = parser.parse_args()
    if args.report_out and (args.report_out.suffix.lower() not in {".json", ".md"} or
                            args.report_out.resolve() == args.investigation.resolve()):
        parser.error("--report-out must be a separate .json or .md file.")
    try:
        data = json.loads(args.investigation.read_text(encoding="utf-8-sig"))
        if args.from_report:
            report = CorrelationReport.model_validate(data)
        else:
            result = correlate(data)
            report = result.report
    except (OSError, ValueError) as exc:
        parser.exit(2, f"Invalid investigation: {exc}\n")
    except RuntimeError as exc:
        parser.exit(1, f"Correlation failed: {exc}\n")
    if args.report_out:
        temporary = None
        try:
            with NamedTemporaryFile(mode="w", dir=args.report_out.parent, delete=False,
                                    encoding="utf-8") as stream:
                temporary = Path(stream.name)
                if args.report_out.suffix.lower() == ".md":
                    stream.write(render_markdown(report))
                else:
                    stream.write(report.model_dump_json(indent=2) + "\n")
            temporary.replace(args.report_out)
        except OSError as exc:
            parser.exit(1, f"Could not save report: {exc}\n")
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)
    if not args.from_report:
        print(result.model_dump_json(indent=2))
    elif args.report_out is None:
        print(render_markdown(report), end="")


if __name__ == "__main__":
    main()
