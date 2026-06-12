from app.core.db import get_conn, close_pool

sql = "select id from generated_reports order by generated_at desc limit 1"

try:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(sql)
        row = cur.fetchone()

        # row may be a tuple OR a mapping-like row
        if row is None:
            print("NONE")
        else:
            try:
                print(row["id"])
            except Exception:
                print(row[0])
finally:
    # Prevent psycopg_pool worker thread warnings in short-lived scripts
    close_pool()
