"""Waste detectors for Kubernetes workloads.

Input model: a "cluster snapshot" — workloads with their resource requests,
limits, and observed usage (e.g. from `kubectl top` / metrics-server),
plus per-unit price rates.

Units: CPU in millicores, memory in MiB. Rates are USD per vCPU-hour and
per GB-hour; monthly cost assumes 730 hours. All dollar figures are
estimates — override rates with your own instance pricing.
"""

from dataclasses import dataclass, field

HOURS_PER_MONTH = 730

# A container is over-provisioned when usage is below this fraction of request.
OVERPROVISIONED_THRESHOLD = 0.5
# A workload is idle when usage is below this fraction of request.
IDLE_THRESHOLD = 0.05
# Suggested requests = usage * HEADROOM, floored at the minimums below.
HEADROOM = 1.5
MIN_CPU_M = 50
MIN_MEM_MI = 64
# Usage above this fraction of the limit is a reliability risk, not waste.
HOT_THRESHOLD = 0.9

DEFAULT_RATES = {"cpu_per_vcpu_hour": 0.04, "memory_per_gb_hour": 0.005}


@dataclass
class Finding:
    check_id: str
    category: str  # waste | risk | info
    title: str
    workload: str  # e.g. "Deployment/prod/api"
    detail: str
    suggestion: str
    monthly_savings_usd: float = 0.0


def _wid(w: dict) -> str:
    return f"{w.get('kind', '?')}/{w.get('namespace', '?')}/{w.get('name', '?')}"


def _cpu_monthly(millicores: float, rates: dict) -> float:
    return (millicores / 1000) * rates["cpu_per_vcpu_hour"] * HOURS_PER_MONTH


def _mem_monthly(mib: float, rates: dict) -> float:
    return (mib / 1024) * rates["memory_per_gb_hour"] * HOURS_PER_MONTH


def _suggested(usage: float, minimum: float) -> float:
    return max(usage * HEADROOM, minimum)


def detect_idle(w: dict, rates: dict):
    """Workloads burning <5% of requested resources: scale-down candidates."""
    replicas = w.get("replicas", 1)
    if replicas is None or replicas < 2:
        return
    containers = w.get("containers", [])
    if not containers:
        return
    ratios = []
    for c in containers:
        req_c, req_m = c.get("cpu_request_m"), c.get("memory_request_mi")
        if not req_c or not req_m:
            return  # can't judge idleness without requests
        ratios.append(max((c.get("cpu_usage_m", 0) / req_c),
                          (c.get("memory_usage_mi", 0) / req_m)))
    if ratios and max(ratios) < IDLE_THRESHOLD:
        per_replica = sum(
            _cpu_monthly(c.get("cpu_request_m", 0), rates)
            + _mem_monthly(c.get("memory_request_mi", 0), rates)
            for c in containers
        )
        yield Finding(
            check_id="IDLE001",
            category="waste",
            title="Idle workload",
            workload=_wid(w),
            detail=(f"{replicas} replicas averaging <5% of requested CPU/memory. "
                    f"~${per_replica * (replicas - 1):.2f}/mo could go with 1 replica."),
            suggestion=("Scale down to 1 replica (or to zero with KEDA/scale-to-zero) "
                        "and watch for a week before deleting."),
            monthly_savings_usd=per_replica * (replicas - 1),
        )


def detect_overprovisioned(w: dict, rates: dict, idle_found: bool):
    """Containers requesting far more than they use: rightsizing candidates."""
    if idle_found:
        return  # idle finding already captures the waste; avoid double counting
    replicas = w.get("replicas", 1) or 1
    for c in w.get("containers", []):
        name = c.get("name")
        for res, unit, req_key, use_key, floor, monthly, label in (
            ("cpu", "m", "cpu_request_m", "cpu_usage_m", MIN_CPU_M, _cpu_monthly, "CPU"),
            ("memory", "Mi", "memory_request_mi", "memory_usage_mi", MIN_MEM_MI, _mem_monthly, "memory"),
        ):
            req, use = c.get(req_key), c.get(use_key, 0)
            if not req:
                continue
            if use < req * OVERPROVISIONED_THRESHOLD:
                suggested = _suggested(use, floor)
                savings = monthly(req - suggested, rates) * replicas
                yield Finding(
                    check_id=f"OVER001-{res}",
                    category="waste",
                    title=f"Over-provisioned {label}",
                    workload=_wid(w),
                    detail=(f"Container '{name}' uses ~{use}{unit} but requests {req}{unit} "
                            f"({use / req:.0%} of request)."),
                    suggestion=f"Lower {label} request toward ~{suggested:.0f}{unit} and observe.",
                    monthly_savings_usd=savings,
                )


def detect_missing_requests(w: dict, rates: dict):
    """Containers without requests break bin-packing: waste you can't measure."""
    for c in w.get("containers", []):
        if not c.get("cpu_request_m") or not c.get("memory_request_mi"):
            yield Finding(
                check_id="REQ001",
                category="info",
                title="Missing resource requests",
                workload=_wid(w),
                detail=(f"Container '{c.get('name')}' has no CPU/memory requests; the scheduler "
                        "can't pack it efficiently and waste is unmeasurable."),
                suggestion="Set requests from observed usage (usage x 1.25 is a sane start).",
            )


def detect_no_hpa(w: dict, rates: dict):
    """Fixed replica counts with no autoscaler: missed elasticity."""
    if w.get("kind") not in ("Deployment", "StatefulSet"):
        return
    replicas = w.get("replicas", 1) or 1
    if replicas >= 3 and not w.get("has_hpa"):
        yield Finding(
            check_id="HPA001",
            category="info",
            title="No autoscaler on a fixed-size workload",
            workload=_wid(w),
            detail=(f"{replicas} fixed replicas with no HPA: pays for peak capacity around the clock."),
            suggestion="Add an HPA (CPU/memory or custom metrics) so idle periods scale down.",
        )


def detect_underprovisioned(w: dict, rates: dict):
    """Usage near limits: throttling / OOMKills waiting to happen (reliability, not waste)."""
    for c in w.get("containers", []):
        name = c.get("name")
        for label, use_key, lim_key in (
            ("CPU", "cpu_usage_m", "cpu_limit_m"),
            ("memory", "memory_usage_mi", "memory_limit_mi"),
        ):
            use, lim = c.get(use_key, 0), c.get(lim_key)
            if lim and use > lim * HOT_THRESHOLD:
                yield Finding(
                    check_id="RISK001",
                    category="risk",
                    title=f"{label} usage near limit",
                    workload=_wid(w),
                    detail=(f"Container '{name}' uses {use} vs limit {lim} "
                            f"({use / lim:.0%}) — throttling or OOMKill risk."),
                    suggestion=f"Raise the {label} limit or investigate the usage spike before it pages you.",
                )


ALL_DETECTORS = [
    detect_idle,
    detect_overprovisioned,  # needs idle_found flag; wired in analyzer
    detect_missing_requests,
    detect_no_hpa,
    detect_underprovisioned,
]
