# k8s-cost-detective

Kubernetes cost detective: feed it a cluster snapshot (workloads with resource
requests, limits, and observed usage) and it finds wasted spend — over-provisioned
containers, idle workloads — and estimates the monthly dollar waste.

## The problem

Kubernetes makes it easy to request 1 CPU "just in case" and forget about it.
Across a cluster, those forgotten requests add up to real money: nodes you pay
for but never use. This tool turns `kubectl top`-style data into a prioritized
savings report.

## What it does (Phase 1)

Analyzes a snapshot JSON file and reports:

- **Over-provisioned CPU/memory** — usage < 50% of request; suggests a
  right-sized request (usage x 1.5 headroom) and the monthly savings
- **Idle workloads** — < 5% utilization with 2+ replicas; suggests scaling down
- **Missing resource requests** — unmeasurable waste; the scheduler can't bin-pack
- **No autoscaler** — fixed replica counts with no HPA pay for peak 24/7
- **Usage near limits** — reliability risk (throttling/OOMKill), flagged not priced

Dollar figures use configurable rates (defaults: $0.04/vCPU-hour,
$0.005/GB-hour, 730 hrs/month) — clearly labeled estimates, not bills.

## Quickstart

```bash
pip install -r requirements.txt

# Human-readable report
python -m src.cli analyze fixtures/sample-snapshot.json

# JSON for automation
python -m src.cli analyze fixtures/sample-snapshot.json --json
```

The sample snapshot finds **~$198.55/month** of waste across 4 workloads.

## Snapshot schema

```json
{
  "rates": {"cpu_per_vcpu_hour": 0.04, "memory_per_gb_hour": 0.005},
  "workloads": [
    {
      "kind": "Deployment", "name": "api", "namespace": "prod",
      "replicas": 4, "has_hpa": false,
      "containers": [
        {"name": "api",
         "cpu_request_m": 1000, "memory_request_mi": 2048,
         "cpu_limit_m": 2000, "memory_limit_mi": 4096,
         "cpu_usage_m": 120, "memory_usage_mi": 400}
      ]
    }
  ]
}
```

CPU in millicores, memory in MiB. Usage typically comes from metrics-server
(`kubectl top pods --containers`).

## Layout

```
src/
  detectors.py  # waste/risk detectors: pure functions over the snapshot
  analyzer.py   # orchestration, savings totals, report model
  cli.py        # `analyze` command: text and JSON reports
fixtures/
  sample-snapshot.json   # 4 workloads -> ~$198.55/mo waste, 3 waste + 1 risk + 3 info
tests/                   # 8 tests: detectors, no-double-counting, snapshot totals
```

## Roadmap

- Phase 2: FastAPI service + Docker packaging
- Phase 3: CI, demo script, snapshot collector (`kubectl` -> snapshot JSON)
- LLM-written savings explanations grounded in the findings (the "detective brief")

## Honest notes

- Savings are estimates from your rates and point-in-time usage; real bills vary.
- Idle detection deliberately suppresses per-container rightsizing on the same
  workload so waste is never double-counted.
