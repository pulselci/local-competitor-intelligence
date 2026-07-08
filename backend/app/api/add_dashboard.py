"""
Run this once to inject the /admin/clients endpoint into routes.py
Usage: python backend/app/api/add_dashboard.py
"""
import re

routes_path = "backend/app/api/routes.py"
content = open(routes_path, "r", encoding="utf-8").read()

MARKER = '@router.get("/admin/billing/success", response_class=HTMLResponse)'

DASHBOARD = '''@router.get("/admin/clients", response_class=HTMLResponse)
def admin_clients_dashboard(key: str = ""):
    import re as _re, traceback as _tb, datetime as _dt
    if key != settings.admin_api_key:
        return HTMLResponse("<h2>Unauthorized</h2>", status_code=401)
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT b.id, b.name, b.city, b.state, b.notes, b.created_at,
                           rs.is_enabled, rs.next_run_at, rs.last_run_at,
                           (SELECT MAX(gr.generated_at) FROM public.generated_reports gr WHERE gr.business_id = b.id) AS last_report_at,
                           (SELECT COUNT(*) FROM public.generated_reports gr WHERE gr.business_id = b.id) AS report_count
                    FROM public.businesses b
                    LEFT JOIN public.report_schedules rs ON rs.business_id = b.id
                    ORDER BY b.created_at DESC
                """)
                rows = cur.fetchall()

        def _email(notes):
            m = _re.search(r"<([^>]+@[^>]+)>", notes or "")
            return m.group(1) if m else ""
        def _cname(notes):
            m = _re.search(r"Contact:\\s*([^<\\n]+)", notes or "")
            return m.group(1).strip() if m else ""
        def _fdt(dt):
            try:
                return dt.strftime("%b %d, %Y") if dt else "---"
            except Exception:
                return str(dt)[:10] if dt else "---"

        subs, prospects = [], []
        for r in rows:
            e = {
                "name": r.get("name") or "---",
                "city": r.get("city") or "---",
                "state": r.get("state") or "---",
                "email": _email(r.get("notes")),
                "contact": _cname(r.get("notes")),
                "created": _fdt(r.get("created_at")),
                "last_report": _fdt(r.get("last_report_at")),
                "next_report": _fdt(r.get("next_run_at")),
                "count": int(r.get("report_count") or 0),
            }
            (subs if r.get("is_enabled") else prospects).append(e)

        def _rows(items, show_next=False):
            if not items:
                return "<tr><td colspan=\\"8\\" style=\\"text-align:center;color:#94a3b8;padding:20px;\\">No records yet</td></tr>"
            out = ""
            for e in items:
                next_td = ("<td>" + e["next_report"] + "</td>") if show_next else "<td></td>"
                out += (
                    "<tr>"
                    "<td>" + e["name"] + "</td>"
                    "<td>" + e["city"] + ", " + e["state"] + "</td>"
                    "<td>" + e["contact"] + "</td>"
                    "<td><a href=\\"mailto:" + e["email"] + "\\" style=\\"color:#2563eb;\\">" + e["email"] + "</a></td>"
                    "<td>" + e["created"] + "</td>"
                    "<td>" + e["last_report"] + "</td>"
                    "<td>" + str(e["count"]) + " sent</td>"
                    + next_td +
                    "</tr>"
                )
            return out

        css = (
            "*{box-sizing:border-box;margin:0;padding:0}"
            "body{font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;background:#f1f5f9;color:#1e293b}"
            ".hdr{background:#10233f;color:white;padding:18px 32px;display:flex;align-items:center;justify-content:space-between}"
            ".hdr h1{font-size:17px;font-weight:800}.hdr span{font-size:12px;opacity:.6}"
            ".wrap{padding:24px 32px;max-width:1200px;margin:0 auto}"
            ".stats{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-bottom:24px}"
            ".stat{background:white;border-radius:10px;padding:16px 20px;box-shadow:0 1px 3px rgba(0,0,0,.08)}"
            ".sv{font-size:28px;font-weight:900;color:#10233f}.sl{font-size:12px;color:#64748b;margin-top:3px}"
            ".card{background:white;border-radius:12px;box-shadow:0 1px 3px rgba(0,0,0,.08);margin-bottom:24px;overflow:hidden}"
            ".ch{padding:14px 20px;border-bottom:1px solid #e2e8f0;display:flex;align-items:center;gap:10px}"
            ".ch h2{font-size:14px;font-weight:700;color:#10233f}"
            ".badge{padding:3px 10px;border-radius:99px;font-size:11px;font-weight:700}"
            ".bs{background:#dcfce7;color:#166534}.bp{background:#fef9c3;color:#854d0e}"
            "table{width:100%;border-collapse:collapse;font-size:13px}"
            "th{padding:9px 16px;text-align:left;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;color:#64748b;background:#f8fafc;border-bottom:1px solid #e2e8f0}"
            "td{padding:11px 16px;border-bottom:1px solid #f1f5f9;color:#334155}"
            "tr:last-child td{border-bottom:none}tr:hover td{background:#f8fafc}"
        )
        now = _fdt(_dt.datetime.utcnow())
        html = (
            "<!DOCTYPE html><html><head><meta charset=\\"UTF-8\\"><title>Pulse LCI Clients</title>"
            "<style>" + css + "</style></head><body>"
            "<div class=\\"hdr\\"><h1>Pulse LCI -- Client Dashboard</h1><span>Updated " + now + "</span></div>"
            "<div class=\\"wrap\\">"
            "<div class=\\"stats\\">"
            "<div class=\\"stat\\"><div class=\\"sv\\">" + str(len(subs)) + "</div><div class=\\"sl\\">Active Subscribers</div></div>"
            "<div class=\\"stat\\"><div class=\\"sv\\">" + str(len(prospects)) + "</div><div class=\\"sl\\">Free Report Prospects</div></div>"
            "<div class=\\"stat\\"><div class=\\"sv\\">" + str(len(subs)+len(prospects)) + "</div><div class=\\"sl\\">Total in System</div></div>"
            "</div>"
            "<div class=\\"card\\"><div class=\\"ch\\"><h2>Active Subscribers</h2>"
            "<span class=\\"badge bs\\">" + str(len(subs)) + " paying</span></div>"
            "<table><thead><tr><th>Business</th><th>Location</th><th>Contact</th><th>Email</th>"
            "<th>Signed Up</th><th>Last Report</th><th>Reports Sent</th><th>Next Report</th></tr></thead>"
            "<tbody>" + _rows(subs, show_next=True) + "</tbody></table></div>"
            "<div class=\\"card\\"><div class=\\"ch\\"><h2>Free Report Prospects</h2>"
            "<span class=\\"badge bp\\">" + str(len(prospects)) + " prospects</span></div>"
            "<table><thead><tr><th>Business</th><th>Location</th><th>Contact</th><th>Email</th>"
            "<th>Requested</th><th>Report Sent</th><th>Reports Sent</th><th></th></tr></thead>"
            "<tbody>" + _rows(prospects) + "</tbody></table></div>"
            "</div></body></html>"
        )
        return HTMLResponse(html)
    except Exception as exc:
        return HTMLResponse(
            "<pre style=\\"padding:20px\\">Error: " + str(exc) + "\\n\\n" + _tb.format_exc() + "</pre>",
            status_code=500
        )


'''

if MARKER in content:
    content = content.replace(MARKER, DASHBOARD + MARKER)
    open(routes_path, "w", encoding="utf-8").write(content)
    print("Done -- dashboard injected")
else:
    print("ERROR: marker not found")
