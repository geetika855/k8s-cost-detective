#!/usr/bin/env bash
# Demo: analyze the sample snapshot via CLI, then exercise the API.
set -euo pipefail
cd "$(dirname "$0")"

PY=python3
if ! $PY -c "import fastapi" 2>/dev/null; then
  echo "creating .venv and installing dependencies..."
  $PY -m venv .venv
  .venv/bin/pip install -q -r requirements.txt
  PY=.venv/bin/python
fi

echo "=== 1. CLI: sample snapshot ==="
$PY -m src.cli analyze fixtures/sample-snapshot.json | head -14

echo ""
echo "=== 2. API: /health and /analyze ==="
$PY - <<'EOF'
import json
from pathlib import Path
from fastapi.testclient import TestClient
from src.api import app

client = TestClient(app)
print("GET /health ->", client.get("/health").json())

snap = json.loads(Path("fixtures/sample-snapshot.json").read_text())
r = client.post("/analyze", json=snap).json()
print(f"POST /analyze -> ${r['total_monthly_savings_usd']:.2f}/mo waste, "
      f"{r['counts']['waste']} waste / {r['counts']['risk']} risk / {r['counts']['info']} info")
print("top finding:", r["findings"][0]["check_id"], r["findings"][0]["title"],
      f"(${r['findings'][0]['monthly_savings_usd']:.2f}/mo)")
EOF

echo ""
echo "=== 3. Tests ==="
$PY -m pytest tests/ -q 2>&1 | tail -1
echo "demo complete"
