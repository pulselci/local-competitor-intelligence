import sys
sys.path.insert(0, ".")
from app.core.config import settings
from app.core.db import get_conn

print(f"DB URL (masked): {str(settings.DATABASE_URL)[:60]}...")

with get_conn() as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM outreach_prospects")
        row = cur.fetchone()
        total = row["n"] if isinstance(row, dict) else row[0]

        cur.execute("SELECT COUNT(*) AS n FROM outreach_prospects WHERE is_test = true")
        row = cur.fetchone()
        test_count = row["n"] if isinstance(row, dict) else row[0]

        cur.execute("SELECT COUNT(*) AS n FROM outreach_prospects WHERE is_test = false OR is_test IS NULL")
        row = cur.fetchone()
        live_count = row["n"] if isinstance(row, dict) else row[0]

        cur.execute("SELECT 1 FROM outreach_prospects WHERE place_id = %s LIMIT 1", ("ChIJKVJwBIQsDogRdvb7lrMjmak",))
        exists = cur.fetchone() is not None

print(f"Total outreach_prospects: {total}")
print(f"  is_test=true:       {test_count}")
print(f"  is_test=false/null: {live_count}")
print(f"Dental Group of Chicago exists in DB: {exists}")
