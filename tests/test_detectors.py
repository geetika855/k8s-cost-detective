"""Tests for the k8s-cost-detective waste detectors."""

from pathlib import Path

import pytest

from src.analyzer import analyze_snapshot, load_snapshot
from src.detectors import (
    detect_idle,
    detect_missing_requests,
    detect_no_hpa,
    detect_overprovisioned,
    detect_underprovisioned,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
RATES = {"cpu_per_vcpu_hour": 0.04, "memory_per_gb_hour": 0.005}


def _w(**kw):
    base = {"kind": "Deployment", "name": "api", "namespace": "prod",
            "replicas": 2, "has_hpa": False, "containers": []}
    base.update(kw)
    return base


def _c(**kw):
    base = {"name": "api", "cpu_request_m": 1000, "memory_request_mi": 1024,
            "cpu_limit_m": 2000, "memory_limit_mi": 2048,
            "cpu_usage_m": 100, "memory_usage_mi": 100}
    base.update(kw)
    return base


def test_overprovisioned_cpu_suggests_rightsize():
    w = _w(containers=[_c(cpu_usage_m=100, cpu_request_m=1000)])
    findings = list(detect_overprovisioned(w, RATES, idle_found=False))
    cpu = [f for f in findings if f.check_id == "OVER001-cpu"]
    assert len(cpu) == 1
    assert cpu[0].monthly_savings_usd > 0
    assert "150m" in cpu[0].suggestion  # 100m usage x 1.5 headroom


def test_healthy_utilization_not_flagged():
    w = _w(containers=[_c(cpu_usage_m=700, cpu_request_m=1000,
                           memory_usage_mi=700, memory_request_mi=1024)])
    assert list(detect_overprovisioned(w, RATES, idle_found=False)) == []


def test_idle_workload_scales_down():
    w = _w(replicas=6, containers=[_c(cpu_usage_m=10, memory_usage_mi=20)])
    findings = list(detect_idle(w, RATES))
    assert len(findings) == 1
    assert findings[0].check_id == "IDLE001"
    # 5 replicas removable x per-replica cost (1000m/1024Mi requested)
    per_replica = (1.0 * 0.04 + 1.0 * 0.005) * 730
    assert findings[0].monthly_savings_usd == pytest.approx(per_replica * 5)


def test_idle_suppresses_rightsize_double_count():
    w = _w(replicas=6, containers=[_c(cpu_usage_m=10, memory_usage_mi=20)])
    assert list(detect_overprovisioned(w, RATES, idle_found=True)) == []


def test_missing_requests_flagged_without_dollars():
    w = _w(containers=[_c(cpu_request_m=None, memory_request_mi=None)])
    findings = list(detect_missing_requests(w, RATES))
    assert len(findings) == 1
    assert findings[0].monthly_savings_usd == 0.0


def test_no_hpa_flagged_for_fixed_replicas():
    assert len(list(detect_no_hpa(_w(replicas=4, has_hpa=False), RATES))) == 1
    assert list(detect_no_hpa(_w(replicas=4, has_hpa=True), RATES)) == []
    assert list(detect_no_hpa(_w(replicas=2, has_hpa=False), RATES)) == []


def test_near_limit_is_risk_not_waste():
    w = _w(containers=[_c(memory_usage_mi=1900, memory_limit_mi=2048)])
    findings = list(detect_underprovisioned(w, RATES))
    assert len(findings) == 1
    assert findings[0].category == "risk"
    assert findings[0].monthly_savings_usd == 0.0


def test_sample_snapshot_totals():
    report = analyze_snapshot(load_snapshot(FIXTURES / "sample-snapshot.json"))
    assert report.workloads_scanned == 4
    counts = report.by_category()
    assert counts == {"waste": 3, "risk": 1, "info": 3}
    # api rightsizing ($95.78 cpu + $20.65 mem) + worker idle ($82.13)
    assert report.total_monthly_savings_usd == pytest.approx(198.55, abs=0.05)
    top = report.sorted_findings()[0]
    assert top.monthly_savings_usd == pytest.approx(95.78, abs=0.05)
