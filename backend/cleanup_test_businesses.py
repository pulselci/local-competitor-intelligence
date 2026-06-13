"""
Cleanup: delete all test businesses and their related data.
Run on July 2nd (or whenever you're ready to go live clean):
    python cleanup_test_businesses.py

Deletes rows where is_test = true from:
  - businesses (cascades to competitors, snapshots, reviews, reports, schedules)

Review the count before confirming.
"""
import sys
sys.path.insert(0, ".")

from psycopg import connect

DATABASE_URL = "postgresql://postgres:32GpboBFaHabEl4w@db.tjjrrehgcbkqagbmfjif.supabase.co:5432/postgres"

with connect(DATABASE_URL, options="-c statement_timeout=0") as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM public.businesses WHERE is_test = true")
        count = cur.fetchone()[0]

print(f"Found {count} test business(es) to delete.")
confirm = input("Type YES to delete them permanently: ")

if confirm.strip() != "YES":
    print("Aborted.")
    sys.exit(0)

with connect(DATABASE_URL, options="-c statement_timeout=0") as conn:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM public.businesses WHERE is_test = true")
        deleted = cur.rowcount
    conn.commit()
    print(f"Deleted {deleted} test business(es). You're live with clean data.")
