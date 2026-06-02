"""
Render Cron Job: monthly report runner.
Schedule: 0 13 1 * *  (8am ET on the 1st of each month)
Command: python backend/cron_run_reports.py
"""
import os
import urllib.request
import urllib.error

API_URL = os.environ.get("API_BASE_URL", "https://pulse-lci-api.onrender.com")
ADMIN_KEY = os.environ.get("ADMIN_API_KEY", "")

req = urllib.request.Request(
    f"{API_URL}/cron/run-scheduled-reports",
    method="POST",
    headers={"X-Admin-Key": ADMIN_KEY, "Content-Length": "0"},
)
try:
    with urllib.request.urlopen(req, timeout=60) as resp:
        print(f"[cron-reports] status={resp.status} response={resp.read().decode()}")
except urllib.error.HTTPError as e:
    print(f"[cron-reports] HTTP error {e.code}: {e.read().decode()}")
except Exception as e:
    print(f"[cron-reports] error: {e}")
