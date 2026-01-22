import { useState } from "react";
import "./App.css";

import AdminView from "./AdminView";
import ClientView from "./ClientView";

export default function App() {
  const [viewMode, setViewMode] = useState("admin"); // "admin" | "client"

  return (
    <div style={{ padding: 16 }}>
      <div
        style={{
          display: "flex",
          gap: 8,
          marginBottom: 12,
          alignItems: "center",
          flexWrap: "wrap",
        }}
      >
        <button
          onClick={() => setViewMode("admin")}
          style={{
            padding: "8px 12px",
            borderRadius: 8,
            border: "1px solid #ccc",
            background: viewMode === "admin" ? "#111" : "#fff",
            color: viewMode === "admin" ? "#fff" : "#111",
            cursor: "pointer",
          }}
        >
          Admin View
        </button>

        <button
          onClick={() => setViewMode("client")}
          style={{
            padding: "8px 12px",
            borderRadius: 8,
            border: "1px solid #ccc",
            background: viewMode === "client" ? "#111" : "#fff",
            color: viewMode === "client" ? "#fff" : "#111",
            cursor: "pointer",
          }}
        >
          Client View
        </button>

        <div style={{ marginLeft: 8, fontSize: 12, opacity: 0.7 }}>
          Mode: <strong>{viewMode}</strong>
        </div>
      </div>

      {viewMode === "client" ? <ClientView /> : <AdminView />}
    </div>
  );
}
