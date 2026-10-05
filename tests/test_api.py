"""API tests: /health and /analyze against the sample snapshot."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.analyzer import load_snapshot
from src.api import app
from src.detectors import ALL_DETECTORS

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
client = TestClient(app)


def _snapshot() -> dict:
    return json.loads((FIXTURES / "sample-snapshot.json").read_text())


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["detectors"] == len(ALL_DETECTORS)


def test_analyze_snapshot_totals():
    snap = _snapshot()
    r = client.post("/analyze", json=snap)
    assert r.status_code == 200
    body = r.json()
    assert body["workloads_scanned"] == 4
    assert body["total_monthly_savings_usd"] == pytest.approx(198.55, abs=0.05)
    assert body["counts"] == {"waste": 3, "risk": 1, "info": 3}
    assert body["findings"][0]["monthly_savings_usd"] == pytest.approx(95.78, abs=0.05)


def test_analyze_custom_rates():
    snap = _snapshot()
    snap["rates"] = {"cpu_per_vcpu_hour": 0.08, "memory_per_gb_hour": 0.01}
    r = client.post("/analyze", json=snap)
    assert r.status_code == 200
    body = r.json()
    assert body["rates"]["cpu_per_vcpu_hour"] == 0.08
    # doubling both rates doubles the savings
    assert body["total_monthly_savings_usd"] == pytest.approx(198.55 * 2, abs=0.1)


def test_analyze_rejects_empty():
    r = client.post("/analyze", json={"workloads": []})
    assert r.status_code == 422


def test_load_snapshot_helper():
    snap = load_snapshot(FIXTURES / "sample-snapshot.json")
    assert len(snap["workloads"]) == 4
