import sys
sys.path.insert(0, ".")
from app.core.config import settings
from psycopg import connect

DATABASE_URL = "postgresql://postgres:32GpboBFaHabEl4w@db.tjjrrehgcbkqagbmfjif.supabase.co:5432/postgres"

with connect(DATABASE_URL, options="-c statement_timeout=0") as conn:
    with conn.cursor() as cur:
        cur.execute("ALTER TABLE public.businesses ADD COLUMN IF NOT EXISTS customer_label TEXT")
    conn.commit()
    print("ALTER done")

with connect(DATABASE_URL, options="-c statement_timeout=0") as conn:
    with conn.cursor() as cur:
        cur.execute("UPDATE public.businesses SET customer_label = 'patients' WHERE name ILIKE '%cedar village%'")
        conn.commit()
    print("UPDATE done")
