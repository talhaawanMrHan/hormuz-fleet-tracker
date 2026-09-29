import React from "react";
import { api } from "../api/client";

const TYPE_LABEL = {
  geofence_breach: "Geofence breach",
  proximity: "Proximity warning",
  predicted_collision: "Predicted collision",
  predicted_zone_entry: "Predicted zone entry",
  predicted_fuel_shortage: "Predicted fuel shortage",
  stranded: "Stranded",
  insufficient_fuel: "Fuel",
  distress: "Distress",
  assistance_request: "Assistance request",
  advisor_suggestion: "Advisor",
};

function timeAgo(ms) {
  const s = Math.max(0, Math.floor((Date.now() - ms) / 1000));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  return `${Math.floor(s / 3600)}h ago`;
}

export default function AlertFeed({ alerts, ships, onChanged, incidentReports }) {
  const shipName = (id) => ships.find((s) => s.id === id)?.name || id;

  async function ack(id) {
    await api.ackAlert(id);
    onChanged();
  }
  async function resolve(id) {
    await api.resolveAlert(id);
    onChanged();
  }

  if (!alerts.length) {
    return <div className="empty-hint">No alerts yet. The fleet is quiet.</div>;
  }

  return (
    <div>
      {alerts.slice(0, 120).map((a) => (
        <div key={a.id} className={`alert-row sev-${a.severity} ${a.resolved ? "resolved" : ""}`}>
          <div className="top">
            <span className="type-label">{TYPE_LABEL[a.type] || a.type}</span>
            <span className="time mono">{timeAgo(a.created_at)}</span>
          </div>
          <div className="msg">{a.message}</div>
          {a.ship_ids?.length > 0 && (
            <div className="mono" style={{ fontSize: 10.5, color: "var(--text-dim)", marginTop: 3 }}>
              {a.ship_ids.map(shipName).join(", ")}
            </div>
          )}
          {incidentReports?.[a.id] && (
            <div style={{ fontSize: 11.5, color: "var(--text-muted)", marginTop: 5, fontStyle: "italic" }}>
              {incidentReports[a.id]}
            </div>
          )}
          {!a.resolved && (
            <div className="actions">
              {!a.acknowledged && <button onClick={() => ack(a.id)}>Acknowledge</button>}
              <button onClick={() => resolve(a.id)}>Resolve</button>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
