# Run this once to add agency outreach columns to outreach_prospects.
# From PowerShell: cd backend && python run_migration_agency.py
import sys
sys.path.insert(0, ".")

from psycopg import connect

DATABASE_URL = "postgresql://postgres:32GpboBFaHabEl4w@db.tjjrrehgcbkqagbmfjif.supabase.co:5432/postgres"

migration = """
ALTER TABLE outreach_prospects
  ADD COLUMN IF NOT EXISTS prospect_type TEXT NOT NULL DEFAULT 'local_business';

ALTER TABLE outreach_prospects
  ADD COLUMN IF NOT EXISTS partnership_type TEXT;
"""

with connect(DATABASE_URL) as conn:
    with conn.cursor() as cur:
        for stmt in migration.split(";"):
            stmt = stmt.strip()
            if stmt and not stmt.startswith("--"):
                print(f"Running: {stmt[:80]}")
                cur.execute(stmt)
    conn.commit()

print("\n✓ Migration complete — agency columns added to outreach_prospects.")
