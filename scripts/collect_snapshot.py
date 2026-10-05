#!/usr/bin/env python3
"""Build a k8s-cost-detective snapshot from a live cluster.

Requires: kubectl configured against a cluster, metrics-server installed
(for usage data). Without metrics-server, specs are collected and usage
is left null with a warning.

Usage:
    python scripts/collect_snapshot.py [--namespace prod] [--rates rates.json] -o snapshot.json
    python -m src.cli analyze snapshot.json
"""

import argparse
import json
import subprocess
import sys


def _kubectl(*args):
    out = subprocess.run(["kubectl", *args], capture_output=True, text=True)
    if out.returncode != 0:
        print(f"kubectl {' '.join(args)} failed:\n{out.stderr}", file=sys.stderr)
        sys.exit(2)
    return json.loads(out.stdout)


def _parse_cpu(s):
    if s is None:
        return None
    s = str(s)
    if s.endswith("m"):
        return float(s[:-1])
    if s.endswith("n"):
        return float(s[:-1]) / 1e6
    if s.endswith("u"):
        return float(s[:-1]) / 1e3
    return float(s) * 1000


def _parse_mem(s):
    if s is None:
        return None
    s = str(s)
    for suffix, factor in (("Ki", 1 / 1024), ("Mi", 1), ("Gi", 1024), ("Ti", 1024 * 1024),
                           ("K", 1 / 1024), ("M", 1), ("G", 1024)):
        if s.endswith(suffix):
            return float(s[: -len(suffix)]) * factor
    return float(s) / (1024 * 1024)


def _res(resources, section, key, parser):
    try:
        return parser(resources.get(section, {}).get(key))
    except (TypeError, ValueError):
        return None


def main():
    p = argparse.ArgumentParser(description="Collect a cost snapshot from a cluster.")
    p.add_argument("-o", "--output", required=True, help="Output snapshot JSON path.")
    p.add_argument("--namespace", default=None, help="Limit to one namespace (default: all).")
    p.add_argument("--rates", default=None, help="JSON file with {cpu_per_vcpu_hour, memory_per_gb_hour}.")
    args = p.parse_args()

    ns = ["-n", args.namespace] if args.namespace else ["-A"]
    kinds = {}
    for kind in ("deployments", "statefulsets"):
        data = _kubectl("get", kind, *ns, "-o", "json")
        for item in data.get("items", []):
            md, spec = item["metadata"], item["spec"]
            podspec = spec["template"]["spec"]
            kinds[(kind, md.get("namespace"), md["name"])] = {
                "kind": "Deployment" if kind == "deployments" else "StatefulSet",
                "name": md["name"],
                "namespace": md.get("namespace"),
                "replicas": spec.get("replicas", 1),
                "has_hpa": False,  # filled below
                "containers": [
                    {
                        "name": c["name"],
                        "cpu_request_m": _res(c.get("resources", {}), "requests", "cpu", _parse_cpu),
                        "memory_request_mi": _res(c.get("resources", {}), "requests", "memory", _parse_mem),
                        "cpu_limit_m": _res(c.get("resources", {}), "limits", "cpu", _parse_cpu),
                        "memory_limit_mi": _res(c.get("resources", {}), "limits", "memory", _parse_mem),
                        "cpu_usage_m": None,
                        "memory_usage_mi": None,
                    }
                    for c in podspec.get("containers", [])
                ],
            }

    # Which workloads have an HPA?
    try:
        hpas = _kubectl("get", "hpa", *ns, "-o", "json")
        for h in hpas.get("items", []):
            t = h["spec"]["scaleTargetRef"]
            key = ("deployments" if t["kind"] == "Deployment" else "statefulsets",
                   h["metadata"].get("namespace"), t["name"])
            if key in kinds:
                kinds[key]["has_hpa"] = True
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001 - HPA lookup is best-effort
        print(f"warning: could not list HPAs: {e}", file=sys.stderr)

    # Per-container usage from metrics-server (best-effort).
    try:
        metrics = _kubectl("get", "--raw", "/apis/metrics.k8s.io/v1beta1/pods")
        usage_sum, usage_n = {}, {}
        for pod in metrics.get("items", []):
            for c in pod.get("containers", []):
                key = (pod["metadata"].get("namespace"), c["name"])
                u = usage_sum.setdefault(key, {"cpu": 0.0, "mem": 0.0})
                u["cpu"] += _parse_cpu(c["usage"]["cpu"])
                u["mem"] += _parse_mem(c["usage"]["memory"])
                usage_n[key] = usage_n.get(key, 0) + 1
        matched = 0
        for (kind, namespace, name), w in kinds.items():
            for c in w["containers"]:
                key = (namespace, c["name"])
                if key in usage_sum:
                    n = usage_n[key]
                    c["cpu_usage_m"] = round(usage_sum[key]["cpu"] / n, 1)
                    c["memory_usage_mi"] = round(usage_sum[key]["mem"] / n, 1)
                    matched += 1
        if matched == 0:
            print("warning: metrics-server returned no matching containers; usage left null.",
                  file=sys.stderr)
    except SystemExit:
        print("warning: metrics-server unavailable; usage left null.", file=sys.stderr)

    rates = {}
    if args.rates:
        with open(args.rates) as fh:
            rates = json.load(fh)

    snapshot = {"rates": rates, "workloads": list(kinds.values())}
    with open(args.output, "w") as fh:
        json.dump(snapshot, fh, indent=2)
    print(f"wrote {args.output}: {len(snapshot['workloads'])} workloads")


if __name__ == "__main__":
    main()
