import { useEffect, useState } from "react";
import { api } from "./api/client";

export default function Login({ onLoggedIn }) {
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");

  const [role, setRole] = useState("client"); // "client" | "admin"
  const [email, setEmail] = useState("");

  const [businesses, setBusinesses] = useState([]);
  const [businessId, setBusinessId] = useState("");

  useEffect(() => {
    let mounted = true;

    async function boot() {
      setLoading(true);
      setErr("");
      try {
        const list = await api.businesses();
        if (!mounted) return;

        const safe = list || [];
        setBusinesses(safe);

        if (safe.length) {
          setBusinessId(safe[0].id);
        } else {
          setBusinessId("");
        }
      } catch (e) {
        if (!mounted) return;
        setErr(String(e?.message || e));
      } finally {
        if (!mounted) return;
        setLoading(false);
      }
    }

    boot();
    return () => {
      mounted = false;
    };
  }, []);

  function submit(e) {
    e.preventDefault();

    if (!role) {
      setErr("Pick a role.");
      return;
    }
    if (role === "client" && !businessId) {
      setErr("No business available to lock this client to.");
      return;
    }

    const session = {
      role,
      email: email || null,
      business_id: role === "client" ? businessId : null,
      created_at: new Date().toISOString(),
    };

    localStorage.setItem("lci_session", JSON.stringify(session));
    onLoggedIn(session);
  }

  return (
    <div style={{ maxWidth: 720, margin: "40px auto", padding: 16, fontFamily: "system-ui" }}>
      <h1 style={{ marginTop: 0 }}>LCI — Login (Phase 1A)</h1>

      <div style={{ opacity: 0.75, marginBottom: 16 }}>
        Fake login for now. Next phase we’ll swap this for Supabase Auth.
      </div>

      {err ? <pre style={{ color: "crimson", whiteSpace: "pre-wrap" }}>{err}</pre> : null}

      <form onSubmit={submit} style={{ border: "1px solid #ddd", borderRadius: 12, padding: 16 }}>
        <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
          <label style={{ display: "flex", flexDirection: "column", gap: 6, minWidth: 220 }}>
            <div style={{ fontSize: 12, opacity: 0.75 }}>Role</div>
            <select value={role} onChange={(e) => setRole(e.target.value)} style={{ padding: 8 }}>
              <option value="client">Client</option>
              <option value="admin">Admin</option>
            </select>
          </label>

          <label style={{ display: "flex", flexDirection: "column", gap: 6, flex: 1, minWidth: 260 }}>
            <div style={{ fontSize: 12, opacity: 0.75 }}>Email (optional)</div>
            <input
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="craig@example.com"
              style={{ padding: 8 }}
            />
          </label>
        </div>

        <div style={{ marginTop: 14 }}>
          <div style={{ fontSize: 12, opacity: 0.75, marginBottom: 6 }}>
            Client business lock (only matters for client role)
          </div>

          <select
            value={businessId}
            onChange={(e) => setBusinessId(e.target.value)}
            disabled={role !== "client" || loading}
            style={{ padding: 8, minWidth: 320 }}
            title={role !== "client" ? "Admin role not locked to a business" : ""}
          >
            {businesses.length === 0 ? (
              <option value="">No businesses found</option>
            ) : (
              businesses.map((b) => (
                <option key={b.id} value={b.id}>
                  {b.name}
                </option>
              ))
            )}
          </select>
        </div>

        <button
          type="submit"
          disabled={loading}
          style={{
            marginTop: 16,
            padding: "10px 14px",
            borderRadius: 10,
            border: "1px solid #ddd",
            background: "#f3f4f6",
            cursor: "pointer",
          }}
        >
          {loading ? "Loading…" : "Log in"}
        </button>
      </form>

      <div style={{ marginTop: 12, fontSize: 12, opacity: 0.75 }}>
        Tip: If this screen errors, it usually means the backend isn’t reachable or CORS is blocking.
      </div>
    </div>
  );
}
