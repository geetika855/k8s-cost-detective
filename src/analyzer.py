"""Load a cluster snapshot, run waste detectors, and total the savings."""

import json
from dataclasses import dataclass, field
from pathlib import Path

from .detectors import (
    DEFAULT_RATES,
    Finding,
    detect_idle,
    detect_missing_requests,
    detect_no_hpa,
    detect_overprovisioned,
    detect_underprovisioned,
)


@dataclass
class Report:
    workloads_scanned: int
    findings: list[Finding] = field(default_factory=list)
    rates: dict = field(default_factory=lambda: dict(DEFAULT_RATES))

    @property
    def total_monthly_savings_usd(self) -> float:
        return round(sum(f.monthly_savings_usd for f in self.findings), 2)

    @property
    def waste_findings(self) -> list[Finding]:
        return [f for f in self.findings if f.category == "waste"]

    def by_category(self) -> dict[str, int]:
        counts = {"waste": 0, "risk": 0, "info": 0}
        for f in self.findings:
            counts[f.category] += 1
        return counts

    def sorted_findings(self) -> list[Finding]:
        return sorted(self.findings, key=lambda f: f.monthly_savings_usd, reverse=True)


def analyze_snapshot(snapshot: dict) -> Report:
    rates = {**DEFAULT_RATES, **snapshot.get("rates", {})}
    findings: list[Finding] = []
    workloads = snapshot.get("workloads", [])
    for w in workloads:
        idle = list(detect_idle(w, rates))
        findings.extend(idle)
        findings.extend(detect_overprovisioned(w, rates, idle_found=bool(idle)))
        findings.extend(detect_missing_requests(w, rates))
        findings.extend(detect_no_hpa(w, rates))
        findings.extend(detect_underprovisioned(w, rates))
    return Report(workloads_scanned=len(workloads), findings=findings, rates=rates)


def load_snapshot(path: Path) -> dict:
    with open(path) as fh:
        return json.load(fh)
