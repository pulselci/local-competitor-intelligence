"""
Patch routes.py to add sandbox (is_test) visual distinction to the client dashboard.

Run from backend/ directory:
    python patch_sandbox_dashboard.py

What it does:
1. Adds b.is_test to the admin/clients SQL query
2. Stores is_test in the entry dict
3. Shows a "Sandbox" badge next to test business names in the table
4. Adds a summary stat card for sandbox vs. live businesses
"""

from pathlib import Path

ROUTES = Path(__file__).resolve().parent / "app" / "api" / "routes.py"

text = ROUTES.read_text(encoding="utf-8")

CHANGES = [
    # 1. Add is_test to the SQL SELECT (after stripe_price_id)
    (
        "                    b.stripe_price_id,\n"
        "                    rs.is_enabled,",
        "                    b.stripe_price_id,\n"
        "                    COALESCE(b.is_test, false) AS is_test,\n"
        "                    rs.is_enabled,",
    ),

    # 2. Add is_test to the entry dict
    (
        '            "plan": plan_name,\n'
        '            "last_report_id": str(row.get("last_report_id") or ""),\n'
        "        }",
        '            "plan": plan_name,\n'
        '            "last_report_id": str(row.get("last_report_id") or ""),\n'
        '            "is_test": bool(row.get("is_test")),\n'
        "        }",
    ),

    # 3. In table_rows, show Sandbox badge next to business name
    (
        '        html = ""\n'
        "        for e in entries:\n"
        "            next_col = f'<td>{e[\"next_run_at\"]}</td>' if show_next else '<td>—</td>'\n"
        "            plan = e.get(\"plan\", \"—\")\n"
        "            plan_color = \"#166534\" if plan == \"Growth\" else \"#1e40af\" if plan == \"Starter\" else \"#64748b\"\n"
        "            plan_badge = f'<span style=\"background:#f0f9ff;color:{plan_color};font-size:11px;font-weight:700;padding:2px 8px;border-radius:99px;\">{plan}</span>'\n"
        "            report_id = e.get(\"last_report_id\", \"\")\n"
        "            report_link = (\n"
        "                f'<a href=\"/generated-reports/{report_id}/pdf\" target=\"_blank\" '\n"
        "                f'style=\"color:#2563eb;font-size:12px;\">View PDF</a>'\n"
        "                if report_id else \"—\"\n"
        "            )\n"
        "            html += (\n"
        "                \"<tr>\"\n"
        "                f'<td>{e[\"name\"]}</td>'",
        '        html = ""\n'
        "        for e in entries:\n"
        "            next_col = f'<td>{e[\"next_run_at\"]}</td>' if show_next else '<td>—</td>'\n"
        "            plan = e.get(\"plan\", \"—\")\n"
        "            plan_color = \"#166534\" if plan == \"Growth\" else \"#1e40af\" if plan == \"Starter\" else \"#64748b\"\n"
        "            plan_badge = f'<span style=\"background:#f0f9ff;color:{plan_color};font-size:11px;font-weight:700;padding:2px 8px;border-radius:99px;\">{plan}</span>'\n"
        "            report_id = e.get(\"last_report_id\", \"\")\n"
        "            report_link = (\n"
        "                f'<a href=\"/generated-reports/{report_id}/pdf\" target=\"_blank\" '\n"
        "                f'style=\"color:#2563eb;font-size:12px;\">View PDF</a>'\n"
        "                if report_id else \"—\"\n"
        "            )\n"
        "            sandbox_badge = ('<span style=\"background:#fef3c7;color:#92400e;font-size:10px;font-weight:700;padding:1px 6px;border-radius:4px;margin-left:6px;vertical-align:middle;\">SANDBOX</span>' if e.get('is_test') else '')\n"
        "            html += (\n"
        "                \"<tr>\"\n"
        "                f'<td>{e[\"name\"]}{sandbox_badge}</td>'",
    ),

    # 4. Update stats cards to show sandbox vs live counts
    (
        "  <div class=\"stats\">\n"
        "    <div class=\"stat\"><div class=\"stat-val\">{len(subscribers)}</div><div class=\"stat-label\">Active Subscribers</div></div>\n"
        "    <div class=\"stat\"><div class=\"stat-val\">{len(prospects)}</div><div class=\"stat-label\">Free Report Prospects</div></div>\n"
        "    <div class=\"stat\"><div class=\"stat-val\">{len(subscribers) + len(prospects)}</div><div class=\"stat-label\">Total in System</div></div>\n"
        "  </div>",
        "  <div class=\"stats\">\n"
        "    <div class=\"stat\"><div class=\"stat-val\">{len(subscribers)}</div><div class=\"stat-label\">Active Subscribers</div></div>\n"
        "    <div class=\"stat\"><div class=\"stat-val\">{len(prospects)}</div><div class=\"stat-label\">Free Report Prospects</div></div>\n"
        "    <div class=\"stat\"><div class=\"stat-val\">{len([e for e in subscribers+prospects if e.get('is_test')])}</div><div class=\"stat-label\" style=\"color:#92400e\">Sandbox (Test) Businesses</div></div>\n"
        "  </div>",
    ),
]

patched = text
for old, new in CHANGES:
    if old not in patched:
        print(f"[WARN] Could not find patch target:\n{old[:80]!r}\n")
        continue
    patched = patched.replace(old, new, 1)
    print(f"[OK] Applied patch: {old[:60]!r}...")

ROUTES.write_text(patched, encoding="utf-8")
print(f"\nDone — wrote {ROUTES}")
