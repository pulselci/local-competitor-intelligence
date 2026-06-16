"""
Migration: add is_test column to businesses and tag all existing records as test.
Run once from the backend/ directory:
    python run_migration_is_test.py
"""
import sys
sys.path.insert(0, ".")

from psycopg import connect

DATABASE_URL = "postgresql://postgres.tjjrrehgcbkqagbmfjif:32GpboBFaHabEl4w@aws-0-us-west-2.pooler.supabase.com:6543/postgres"

with connect(DATABASE_URL, options="-c statement_timeout=0") as conn:
    with conn.cursor() as cur:
        cur.execute("""
            ALTER TABLE public.businesses
                ADD COLUMN IF NOT EXISTS is_test BOOLEAN NOT NULL DEFAULT false
        """)
        cur.execute("UPDATE public.businesses SET is_test = true")
        count = cur.rowcount
    conn.commit()
    print(f"Done — {count} existing business(es) tagged as is_test = true")
