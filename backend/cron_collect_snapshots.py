"""
Render Cron Job: daily snapshot collection.
Schedule: 0 6 * * *
Command: python backend/cron_collect_snapshots.py
"""
import os
import urllib.request
import urllib.error

API_URL = os.environ.get("API_BASE_URL", "https://pulse-lci-api.onrender.com")
ADMIN_KEY = os.environ.get("ADMIN_API_KEY", "")

req = urllib.request.Request(
    f"{API_URL}/cron/collect-snapshots",
    method="POST",
    headers={"X-Admin-Key": ADMIN_KEY, "Content-Length": "0"},
)
try:
    with urllib.request.urlopen(req, timeout=30) as resp:
        print(f"[cron-snapshots] status={resp.status} response={resp.read().decode()}")
except urllib.error.HTTPError as e:
    print(f"[cron-snapshots] HTTP error {e.code}: {e.read().decode()}")
except Exception as e:
    print(f"[cron-snapshots] error: {e}")
