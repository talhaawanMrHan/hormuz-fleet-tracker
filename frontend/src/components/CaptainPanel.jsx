import React, { useState } from "react";
import { api } from "../api/client";

export default function CaptainPanel({ ship, directives, onChanged, ports = {} }) {
  const [escalatingId, setEscalatingId] = useState(null);
  const [escalateText, setEscalateText] = useState("");
  const [distressText, setDistressText] = useState("");
  const [assistKind, setAssistKind] = useState("medical_aid");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");

  if (!ship) return <div className="empty-hint">No ship assigned.</div>;

  const pending = Object.values(directives).filter((d) => d.ship_id === ship.id && d.status === "pending");

  async function accept(d) {
    setBusy(true);
    try {
      await api.captainRespond(d.id, ship.id, "accept");
      onChanged();
    } finally {
      setBusy(false);
    }
  }

  async function submitEscalate(d) {
    setBusy(true);
    try {
      await api.captainRespond(d.id, ship.id, "escalate_distress", escalateText);
      setEscalatingId(null);
      setEscalateText("");
      onChanged();
    } finally {
      setBusy(false);
    }
  }

  async function submitDistress() {
    if (!distressText.trim()) return;
    setBusy(true);
    try {
      await api.reportDistress(ship.id, distressText);
      setDistressText("");
      setNotice("Distress report sent to Command.");
      setTimeout(() => setNotice(""), 4000);
      onChanged();
    } finally {
      setBusy(false);
    }
  }

  async function requestAssist() {
    setBusy(true);
    try {
      await api.requestAssistance(ship.id, assistKind);
      setNotice("Assistance request broadcast to nearby ships.");
      setTimeout(() => setNotice(""), 4000);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="detail-block scroll" style={{ maxHeight: "100%" }}>
      <div style={{ fontSize: 15, fontWeight: 600 }}>{ship.name} — Bridge</div>
      <div className="mono" style={{ fontSize: 11, color: "var(--text-dim)" }}>IMO {ship.imo}</div>

      <div className="kv-grid">
        <div><div className="k">Status</div><div className={`v status-chip status-${ship.status}`} style={{ display: "inline-block" }}>{ship.status.replace("_", " ")}</div></div>
        <div><div className="k">Fuel</div><div className="v">{ship.fuel_tons.toFixed(0)} t ({ship.fuel_percent.toFixed(0)}%) {ship.fuel_warning ? "⚠" : ""}</div></div>
        <div><div className="k">Speed (SOG)</div><div className="v">{ship.speed_knots.toFixed(1)} kn</div></div>
        <div><div className="k">Heading (COG)</div><div className="v">{Math.round(ship.heading)}°</div></div>
        <div><div className="k">Destination</div><div className="v">{ports?.[ship.destination_port] || ship.destination_port}</div></div>
        <div><div className="k">Distance left</div><div className="v">{Math.round(ship.distance_remaining_km)} km</div></div>
      </div>

      {pending.length > 0 && (
        <>
          <label className="field-label">Directives from Command</label>
          {pending.map((d) => (
            <div className="directive-card" key={d.id}>
              <div className="title">{d.action.replace(/_/g, " ")}</div>
              <div className="body">
                {d.action === "reroute_to_port" && `New destination: ${ports?.[d.target_port] || d.target_port}`}
                {d.action === "divert_to_waypoint" && `Divert to waypoint ${d.waypoint?.[1]?.toFixed(2)}, ${d.waypoint?.[0]?.toFixed(2)}`}
                {d.action === "hold_position" && "Hold current position"}
              </div>
              {escalatingId === d.id ? (
                <div>
                  <textarea
                    rows={3}
                    placeholder="Describe the emergency — what's wrong, injuries, estimated damage…"
                    value={escalateText}
                    onChange={(e) => setEscalateText(e.target.value)}
                  />
                  <div className="btn-row">
                    <button className="btn danger" disabled={busy || !escalateText.trim()} onClick={() => submitEscalate(d)}>
                      Send ESCALATE_DISTRESS
                    </button>
                    <button className="btn" onClick={() => setEscalatingId(null)}>Cancel</button>
                  </div>
                </div>
              ) : (
                <div className="btn-row">
                  <button className="btn primary" disabled={busy} onClick={() => accept(d)}>ACCEPT</button>
                  <button className="btn danger" disabled={busy} onClick={() => setEscalatingId(d.id)}>ESCALATE_DISTRESS</button>
                </div>
              )}
            </div>
          ))}
        </>
      )}

      <label className="field-label">Report distress</label>
      <textarea
        rows={3}
        placeholder="Free-form distress message — e.g. 'engine room fire, 2 crew injured, requesting assistance'"
        value={distressText}
        onChange={(e) => setDistressText(e.target.value)}
      />
      <div className="btn-row">
        <button className="btn danger" disabled={busy || !distressText.trim()} onClick={submitDistress}>Send distress call</button>
      </div>

      <label className="field-label">Request assistance from nearby ships</label>
      <div style={{ display: "flex", gap: 8 }}>
        <select className="ship-select" style={{ flex: 1 }} value={assistKind} onChange={(e) => setAssistKind(e.target.value)}>
          <option value="fuel_transfer">Fuel transfer</option>
          <option value="medical_aid">Medical aid</option>
          <option value="escort">Escort</option>
          <option value="cargo_offload">Cargo offload</option>
        </select>
        <button className="btn" disabled={busy} onClick={requestAssist}>Request</button>
      </div>

      {notice && <div style={{ color: "var(--ok-green)", fontSize: 11.5, marginTop: 8 }}>{notice}</div>}
    </div>
  );
}
