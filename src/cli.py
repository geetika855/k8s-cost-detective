"""CLI: detect Kubernetes cost waste from a cluster snapshot.

Usage:
    python -m src.cli analyze <snapshot.json>            # human-readable report
    python -m src.cli analyze <snapshot.json> --json     # machine-readable JSON

Snapshot schema: {"rates": {...}, "workloads": [...]}, see fixtures/.
CPU in millicores, memory in MiB. See src/detectors.py for the model.
"""

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.analyzer import analyze_snapshot, load_snapshot  # noqa: E402


def _print_text(report):
    counts = report.by_category()
    print(f"k8s-cost-detective  |  workloads: {report.workloads_scanned}")
    print(f"estimated waste: ${report.total_monthly_savings_usd:.2f}/month")
    print(f"findings: {counts['waste']} waste, {counts['risk']} risk, {counts['info']} info")
    print("-" * 70)
    if not report.findings:
        print("No waste detected. Your cluster is lean.")
        return
    for f in report.sorted_findings():
        tag = f"${f.monthly_savings_usd:.2f}/mo" if f.monthly_savings_usd else f.category.upper()
        print(f"[{tag:>12}] {f.check_id}  {f.title}")
        print(f"               workload: {f.workload}")
        print(f"               detail:   {f.detail}")
        print(f"               fix:      {f.suggestion}")
        print()


def main(argv=None):
    parser = argparse.ArgumentParser(prog="k8s-cost-detective",
                                     description="Find wasted spend in Kubernetes workloads.")
    sub = parser.add_subparsers(dest="command", required=True)
    analyze = sub.add_parser("analyze", help="Analyze a cluster snapshot JSON file.")
    analyze.add_argument("snapshot", help="Path to the snapshot JSON file.")
    analyze.add_argument("--json", action="store_true", help="Emit the report as JSON.")
    args = parser.parse_args(argv)

    path = Path(args.snapshot)
    if not path.exists():
        print(f"error: file not found: {path}", file=sys.stderr)
        return 2

    report = analyze_snapshot(load_snapshot(path))
    if args.json:
        print(json.dumps({
            "workloads_scanned": report.workloads_scanned,
            "total_monthly_savings_usd": report.total_monthly_savings_usd,
            "counts": report.by_category(),
            "rates": report.rates,
            "findings": [asdict(f) for f in report.sorted_findings()],
        }, indent=2))
    else:
        _print_text(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
