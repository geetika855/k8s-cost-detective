"""FastAPI service: analyze Kubernetes cost waste over HTTP.

Endpoints:
    GET  /health    service status + detector count
    POST /analyze   {"workloads": [...], "rates": {...}} -> waste report
"""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .analyzer import analyze_snapshot
from .detectors import ALL_DETECTORS

app = FastAPI(title="k8s-cost-detective", version="0.2.0")


class AnalyzeRequest(BaseModel):
    workloads: list[dict] = Field(..., description="Workload dicts; CPU in millicores, memory in MiB.")
    rates: dict = Field(default_factory=dict,
                        description="Optional override: {cpu_per_vcpu_hour, memory_per_gb_hour}.")


@app.get("/health")
def health():
    return {"status": "ok", "detectors": len(ALL_DETECTORS), "version": app.version}


@app.post("/analyze")
def analyze(req: AnalyzeRequest):
    if not req.workloads:
        raise HTTPException(status_code=422, detail="No workloads provided.")
    report = analyze_snapshot({"workloads": req.workloads, "rates": req.rates})
    return {
        "workloads_scanned": report.workloads_scanned,
        "total_monthly_savings_usd": report.total_monthly_savings_usd,
        "counts": report.by_category(),
        "rates": report.rates,
        "findings": [
            {
                "check_id": f.check_id,
                "category": f.category,
                "title": f.title,
                "workload": f.workload,
                "detail": f.detail,
                "suggestion": f.suggestion,
                "monthly_savings_usd": round(f.monthly_savings_usd, 2),
            }
            for f in report.sorted_findings()
        ],
    }
