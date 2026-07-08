from app.core.db import get_conn
import app.core.db as db

BUSINESS_ID = "29a52299-3d54-41a1-a5c5-dcf95d3efab7"
PREV_ID = "341158a0-8649-4898-a5ef-7e6b2cdadeda"
NEW_ID  = "1b338265-eca7-4f0d-8fa8-f832e61f374f"

PREWITT_ID = "77fd95bc-b36c-40e6-a5ed-e6e39ee11ba4"
OWNER_ID   = "fc5e3513-8be1-4b58-a9c6-874553060bd4"

def find_snapshot_table(cur):
    # look for a table with competitor_id + observed_at + reviews_total
    cur.execute("""
        select c.table_name
        from information_schema.columns c
        where c.table_schema='public'
          and c.column_name in ('competitor_id','observed_at','reviews_total')
          and c.table_name ilike '%snap%'
        group by c.table_name
        having count(*) >= 3
        order by c.table_name
    """)
    rows = cur.fetchall()
    tables = [(r["table_name"] if isinstance(r, dict) else r[0]) for r in rows]
    return tables[0] if tables else None

try:
    with get_conn() as conn:
        with conn.cursor() as cur:
            snap_tbl = find_snapshot_table(cur)
            if not snap_tbl:
                raise SystemExit("Could not auto-find snapshot table. Tell me your snapshot table name and columns.")

            print("snapshot_table =", snap_tbl)

            # pull period_end values from the two generated_reports
            cur.execute("select id, period_end from generated_reports where id in (%s,%s)", (PREV_ID, NEW_ID))
            ends = cur.fetchall()
            # normalize into dict id->period_end
            end_map = {}
            for r in ends:
                rid = str(r["id"]) if isinstance(r, dict) else str(r[0])
                pend = r["period_end"] if isinstance(r, dict) else r[1]
                end_map[rid] = pend

            for label, rid in (("PREV", PREV_ID), ("NEW", NEW_ID)):
                pend = end_map.get(rid)
                print("")
                print(f"{label}_period_end =", pend)

                for cid, cname in ((PREWITT_ID,"Prewitt"), (OWNER_ID,"Owner")):
                    cur.execute(f"""
                        select competitor_id, observed_at, reviews_total
                        from {snap_tbl}
                        where competitor_id=%s and observed_at <= %s
                        order by observed_at desc
                        limit 1
                    """, (cid, pend))
                    row = cur.fetchone()
                    print(cname, "latest_snapshot_at_or_before_period_end =", row)

finally:
    if hasattr(db, "close_pool"):
        db.close_pool()
        print("[pool] closed")
