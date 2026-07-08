"""
Migration: add is_test to outreach_prospects and tag all existing records.
Run from backend/:
    python run_migration_outreach_is_test.py
"""
import sys
sys.path.insert(0, ".")

from psycopg import connect

DATABASE_URL = "postgresql://postgres.tjjrrehgcbkqagbmfjif:32GpboBFaHabEl4w@aws-0-us-west-2.pooler.supabase.com:6543/postgres"

with connect(DATABASE_URL, options="-c statement_timeout=0") as conn:
    with conn.cursor() as cur:
        cur.execute("""
            ALTER TABLE public.outreach_prospects
                ADD COLUMN IF NOT EXISTS is_test BOOLEAN NOT NULL DEFAULT false
        """)
        cur.execute("UPDATE public.outreach_prospects SET is_test = true")
        count = cur.rowcount
    conn.commit()
    print(f"Done — {count} outreach prospect(s) tagged as is_test = true")
