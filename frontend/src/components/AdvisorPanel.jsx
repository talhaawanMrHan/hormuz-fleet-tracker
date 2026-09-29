import React, { useState } from "react";
import { api } from "../api/client";

export default function AdvisorPanel({ suggestions, onRefresh }) {
  const [busy, setBusy] = useState(false);

  async function run() {
    setBusy(true);
    try {
      await api.runAdvisor();
      onRefresh?.();
    } finally {
      setBusy(false);
    }
  }

  const list = suggestions?.suggestions || [];

  return (
    <div>
      <div style={{ padding: "0 14px 10px" }}>
        <button className="btn primary" style={{ width: "100%" }} disabled={busy} onClick={run}>
          {busy ? "Thinking…" : "Run AI fleet advisor"}
        </button>
        {suggestions && !suggestions.ai_generated && (
          <div style={{ fontSize: 10.5, color: "var(--text-dim)", marginTop: 6 }}>
            Rule-based fallback (set GROQ_API_KEY for AI-generated suggestions)
          </div>
        )}
      </div>
      {list.length === 0 ? (
        <div className="empty-hint">No suggestions yet — run the advisor.</div>
      ) : (
        list.map((s, i) => (
          <div className="advisor-suggestion" key={i}>
            <span className={`priority ${s.priority}`}>{s.priority}</span>
            <div>{s.action}</div>
            <div className="reasoning">{s.reasoning}</div>
          </div>
        ))
      )}
    </div>
  );
}
