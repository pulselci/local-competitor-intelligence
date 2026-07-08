"""
Clears all outreach_prospects records (they were all test sends).
Run from backend/:
    python clear_outreach_prospects.py
"""
import sys
sys.path.insert(0, ".")

from psycopg import connect

DATABASE_URL = "postgresql://postgres.tjjrrehgcbkqagbmfjif:32GpboBFaHabEl4w@aws-0-us-west-2.pooler.supabase.com:6543/postgres"

with connect(DATABASE_URL, options="-c statement_timeout=0") as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM public.outreach_prospects")
        before = cur.fetchone()[0]
        cur.execute("DELETE FROM public.outreach_prospects")
        deleted = cur.rowcount
    conn.commit()
    print(f"Deleted {deleted} record(s) (was {before} total). Outreach queue is now empty.")
