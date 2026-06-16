"""
Patch routes.py: split sandbox businesses into a separate tab on /admin/clients.
- Live stats only count real (non-test) businesses
- Active Subscribers / Free Report Prospects tabs show only real businesses
- New "Sandbox" tab shows all 23 test businesses
- Pipelines (snapshots, reports, emails) are unaffected

Run from backend/:
    python patch_sandbox_tab.py
"""
from pathlib import Path

ROUTES = Path(__file__).resolve().parent / "app" / "api" / "routes.py"
text = ROUTES.read_text(encoding="utf-8")

CHANGES = [

    # ── 1. Separate sandbox from real businesses ────────────────────────────
    (
        "    prospects = []\n"
        "    subscribers = []\n"
        "\n"
        "    for row in rows:\n"
        "        is_enabled = row.get(\"is_enabled\")\n",

        "    prospects = []\n"
        "    subscribers = []\n"
        "    sandbox = []\n"
        "\n"
        "    for row in rows:\n"
        "        is_enabled = row.get(\"is_enabled\")\n",
    ),

    (
        "        if is_enabled:\n"
        "            subscribers.append(entry)\n"
        "        else:\n"
        "            prospects.append(entry)\n",

        "        if entry[\"is_test\"]:\n"
        "            sandbox.append(entry)\n"
        "        elif is_enabled:\n"
        "            subscribers.append(entry)\n"
        "        else:\n"
        "            prospects.append(entry)\n",
    ),

    # ── 2. Add tab CSS ──────────────────────────────────────────────────────
    (
        "  .stat-val {{ font-size: 28px; font-weight: 900; color: #10233f; }}\n"
        "  .stat-label {{ font-size: 12px; color: #64748b; margin-top: 4px; }}\n"
        "</style>",

        "  .stat-val {{ font-size: 28px; font-weight: 900; color: #10233f; }}\n"
        "  .stat-label {{ font-size: 12px; color: #64748b; margin-top: 4px; }}\n"
        "  .tabs {{ display: flex; gap: 4px; margin-bottom: 20px; }}\n"
        "  .tab-btn {{ padding: 8px 18px; border-radius: 8px 8px 0 0; font-size: 13px; font-weight: 700;\n"
        "             cursor: pointer; border: none; background: #e2e8f0; color: #64748b; }}\n"
        "  .tab-btn.active {{ background: white; color: #10233f; box-shadow: 0 1px 4px rgba(0,0,0,0.08); }}\n"
        "  .tab-panel {{ display: none; }}\n"
        "  .tab-panel.active {{ display: block; }}\n"
        "  .badge-sandbox {{ background: #fef3c7; color: #92400e; }}\n"
        "</style>",
    ),

    # ── 3. Update stats to show only real businesses ────────────────────────
    (
        "  <div class=\"stats\">\n"
        "    <div class=\"stat\"><div class=\"stat-val\">{len(subscribers)}</div><div class=\"stat-label\">Active Subscribers</div></div>\n"
        "    <div class=\"stat\"><div class=\"stat-val\">{len(prospects)}</div><div class=\"stat-label\">Free Report Prospects</div></div>\n"
        "    <div class=\"stat\"><div class=\"stat-val\">{len([e for e in subscribers+prospects if e.get('is_test')])}</div><div class=\"stat-label\" style=\"color:#92400e\">Sandbox (Test) Businesses</div></div>\n"
        "  </div>",

        "  <div class=\"stats\">\n"
        "    <div class=\"stat\"><div class=\"stat-val\">{len(subscribers)}</div><div class=\"stat-label\">Active Subscribers (Live)</div></div>\n"
        "    <div class=\"stat\"><div class=\"stat-val\">{len(prospects)}</div><div class=\"stat-label\">Free Report Prospects (Live)</div></div>\n"
        "    <div class=\"stat\"><div class=\"stat-val\">{len(sandbox)}</div><div class=\"stat-label\" style=\"color:#92400e\">Sandbox Businesses</div></div>\n"
        "  </div>",
    ),

    # ── 4. Replace the two section divs with a tabbed layout ───────────────
    (
        "  <div class=\"section\">\n"
        "    <div class=\"section-header\">\n"
        "      <h2>Active Subscribers</h2>\n"
        "      <span class=\"badge badge-sub\">{len(subscribers)} paying</span>\n"
        "    </div>\n"
        "    <table>\n"
        "      <thead><tr>\n"
        "        <th>Business</th><th>Location</th><th>Contact</th><th>Email</th><th>Plan</th>\n"
        "        <th>Signed Up</th><th>Last Report</th><th>Reports Sent</th><th>Last Report</th><th>Next Report</th>\n"
        "      </tr></thead>\n"
        "      <tbody>{table_rows(subscribers, show_next=True)}</tbody>\n"
        "    </table>\n"
        "  </div>\n"
        "\n"
        "  <div class=\"section\">\n"
        "    <div class=\"section-header\">\n"
        "      <h2>Free Report Prospects</h2>\n"
        "      <span class=\"badge badge-prospect\">{len(prospects)} prospects</span>\n"
        "    </div>\n"
        "    <table>\n"
        "      <thead><tr>\n"
        "        <th>Business</th><th>Location</th><th>Contact</th><th>Email</th><th>Plan</th>\n"
        "        <th>Requested</th><th>Last Report</th><th>Reports Sent</th><th>Last Report</th><th></th>\n"
        "      </tr></thead>\n"
        "      <tbody>{table_rows(prospects, show_next=False)}</tbody>\n"
        "    </table>\n"
        "  </div>\n"
        "</div>\n"
        "</body>\n"
        "</html>\"\"\"",

        "  <!-- Tab buttons -->\n"
        "  <div class=\"tabs\">\n"
        "    <button class=\"tab-btn active\" onclick=\"showTab('live', this)\">Live Clients</button>\n"
        "    <button class=\"tab-btn\" onclick=\"showTab('sandbox', this)\">🧪 Sandbox ({len(sandbox)})</button>\n"
        "  </div>\n"
        "\n"
        "  <!-- Live tab -->\n"
        "  <div class=\"tab-panel active\" id=\"tab-live\">\n"
        "    <div class=\"section\">\n"
        "      <div class=\"section-header\">\n"
        "        <h2>Active Subscribers</h2>\n"
        "        <span class=\"badge badge-sub\">{len(subscribers)} paying</span>\n"
        "      </div>\n"
        "      <table>\n"
        "        <thead><tr>\n"
        "          <th>Business</th><th>Location</th><th>Contact</th><th>Email</th><th>Plan</th>\n"
        "          <th>Signed Up</th><th>Last Report</th><th>Reports Sent</th><th>Last Report</th><th>Next Report</th>\n"
        "        </tr></thead>\n"
        "        <tbody>{table_rows(subscribers, show_next=True)}</tbody>\n"
        "      </table>\n"
        "    </div>\n"
        "    <div class=\"section\">\n"
        "      <div class=\"section-header\">\n"
        "        <h2>Free Report Prospects</h2>\n"
        "        <span class=\"badge badge-prospect\">{len(prospects)} prospects</span>\n"
        "      </div>\n"
        "      <table>\n"
        "        <thead><tr>\n"
        "          <th>Business</th><th>Location</th><th>Contact</th><th>Email</th><th>Plan</th>\n"
        "          <th>Requested</th><th>Last Report</th><th>Reports Sent</th><th>Last Report</th><th></th>\n"
        "        </tr></thead>\n"
        "        <tbody>{table_rows(prospects, show_next=False)}</tbody>\n"
        "      </table>\n"
        "    </div>\n"
        "  </div>\n"
        "\n"
        "  <!-- Sandbox tab -->\n"
        "  <div class=\"tab-panel\" id=\"tab-sandbox\">\n"
        "    <div class=\"section\">\n"
        "      <div class=\"section-header\">\n"
        "        <h2>🧪 Sandbox Businesses</h2>\n"
        "        <span class=\"badge badge-sandbox\">{len(sandbox)} test accounts</span>\n"
        "        <span style=\"font-size:12px;color:#64748b;margin-left:auto;\">Snapshots &amp; reports run normally · not counted in live stats</span>\n"
        "      </div>\n"
        "      <table>\n"
        "        <thead><tr>\n"
        "          <th>Business</th><th>Location</th><th>Email</th><th>Plan</th>\n"
        "          <th>Last Report</th><th>Reports Sent</th><th>Last Report</th><th>Next Report</th>\n"
        "        </tr></thead>\n"
        "        <tbody>{table_rows_sandbox(sandbox)}</tbody>\n"
        "      </table>\n"
        "    </div>\n"
        "  </div>\n"
        "\n"
        "</div>\n"
        "<script>\n"
        "function showTab(name, btn) {{\n"
        "  document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));\n"
        "  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));\n"
        "  document.getElementById('tab-' + name).classList.add('active');\n"
        "  btn.classList.add('active');\n"
        "}}\n"
        "</script>\n"
        "</body>\n"
        "</html>\"\"\"",
    ),
]


# ── Also inject table_rows_sandbox helper before table_rows ────────────────
SANDBOX_HELPER = '''    def table_rows_sandbox(entries):
        if not entries:
            return '<tr><td colspan="8" style="text-align:center;color:#94a3b8;padding:20px;">No sandbox businesses</td></tr>'
        html = ""
        for e in entries:
            next_run = e.get("next_run_at", "—")
            plan = e.get("plan", "—")
            plan_color = "#166534" if plan == "Growth" else "#1e40af" if plan == "Starter" else "#64748b"
            plan_badge = f\'<span style="background:#f0f9ff;color:{plan_color};font-size:11px;font-weight:700;padding:2px 8px;border-radius:99px;">{plan}</span>\'
            report_id = e.get("last_report_id", "")
            report_link = (
                f\'<a href="/generated-reports/{report_id}/pdf" target="_blank" style="color:#2563eb;font-size:12px;">View PDF</a>\'
                if report_id else "—"
            )
            html += (
                "<tr>"
                f\'<td>{e["name"]}</td>\'
                f\'<td>{e["city"]}, {e["state"]}</td>\'
                f\'<td><a href="mailto:{e["contact_email"]}" style="color:#2563eb;">{e["contact_email"]}</a></td>\'
                f\'<td>{plan_badge}</td>\'
                f\'<td>{e["last_report_at"]}</td>\'
                f\'<td>{e["report_count"]} sent</td>\'
                f\'<td>{report_link}</td>\'
                f\'<td>{next_run}</td>\'
                "</tr>"
            )
        return html

'''

INJECT_BEFORE = "    def table_rows(entries, show_next=False):"

patched = text
for old, new in CHANGES:
    if old not in patched:
        print(f"[WARN] Could not find:\n  {old[:80]!r}")
        continue
    patched = patched.replace(old, new, 1)
    print(f"[OK] {old[:60]!r}...")

if INJECT_BEFORE not in patched:
    print(f"[WARN] Could not find injection point for table_rows_sandbox")
else:
    patched = patched.replace(INJECT_BEFORE, SANDBOX_HELPER + INJECT_BEFORE, 1)
    print(f"[OK] Injected table_rows_sandbox helper")

ROUTES.write_text(patched, encoding="utf-8")
print(f"\nDone — wrote {ROUTES}")
