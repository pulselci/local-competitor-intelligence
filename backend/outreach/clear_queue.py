"""
Clears the outreach prospect queue without touching sent/converted records.
Run from the backend directory: python -m outreach.clear_queue
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.db import get_conn

with get_conn() as conn:
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM outreach_prospects WHERE status NOT IN ('sent', 'converted')"
        )
        deleted = cur.rowcount
    conn.commit()

print(f"Cleared {deleted} prospects (sent/converted records preserved).")
