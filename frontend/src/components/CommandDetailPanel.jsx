import React, { useState } from "react";
import { api } from "../api/client";

export default function CommandDetailPanel({ ship, ports, onChanged }) {
  const [action, setAction] = useState("reroute_to_port");
  const [targetPort, setTargetPort] = useState("");
  const [wpLat, setWpLat] = useState("");
  const [wpLng, setWpLng] = useState("");
  const [busy, setBusy] = useState(false);
  const [routeOptions, setRouteOptions] = useState(null);
  const [err, setErr] = useState("");

  if (!ship) return <div className="empty-hint">Select a ship on the map or in the list.</div>;

  async function issueDirective() {
    setErr("");
    setBusy(true);
    try {
      let waypoint;
      if (action === "divert_to_waypoint") {
        const lat = Number(wpLat);
        const lng = Number(wpLng);
        if (!Number.isFinite(lat) || !Number.isFinite(lng) || lat < -90 || lat > 90 || lng < -180 || lng > 180) {
          throw new Error("Enter a valid waypoint: latitude -90..90 and longitude -180..180.");
        }
        waypoint = [lng, lat];
      }
      await api.createDirective(ship.id, action, action === "reroute_to_port" ? targetPort : undefined, waypoint);
      onChanged();
    } catch (e) {
      setErr(String(e.message || e));
    } finally {
      setBusy(false);
    }
  }

  async function loadRouteOptions() {
    setErr("");
    setBusy(true);
    try {
      const d = await api.getRouteOptions(ship.id);
      setRouteOptions(d.options);
    } catch (e) {
      setErr(String(e.message || e));
    } finally {
      setBusy(false);
    }
  }

  async function useRoute(opt) {
    setBusy(true);
    try {
      await api.selectRoute(ship.id, opt.waypoints);
      setRouteOptions(null);
      onChanged();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="detail-block scroll" style={{ maxHeight: "100%" }}>
      <div style={{ fontSize: 15, fontWeight: 600 }}>{ship.name}</div>
      <div className="mono" style={{ fontSize: 11, color: "var(--text-dim)" }}>IMO {ship.imo} · {ship.cargo.replace("_", " ")}</div>
      <div style={{ fontSize: 11.5, color: "var(--text-muted)", marginTop: 3 }}>{ship.cargo_detail}</div>

      <div className="kv-grid">
        <div><div className="k">Status</div><div className={`v status-chip status-${ship.status}`} style={{ display: "inline-block" }}>{ship.status.replace("_", " ")}</div></div>
        <div><div className="k">Fuel</div><div className="v">{ship.fuel_tons.toFixed(0)} t ({ship.fuel_percent.toFixed(0)}%) {ship.fuel_warning ? "⚠" : ""}</div></div>
        <div><div className="k">Speed</div><div className="v">{ship.speed_knots.toFixed(1)} kn</div></div>
        <div><div className="k">Heading</div><div className="v">{Math.round(ship.heading)}°</div></div>
        <div><div className="k">Destination</div><div className="v">{ports?.[ship.destination_port] || ship.destination_port}</div></div>
        <div><div className="k">Distance left</div><div className="v">{Math.round(ship.distance_remaining_km)} km</div></div>
        <div><div className="k">Weather</div><div className="v">{ship.in_adverse_weather ? "Adverse" : "Clear"}</div></div>
        <div><div className="k">Position</div><div className="v" style={{ fontSize: 11 }}>{ship.position[1].toFixed(3)}, {ship.position[0].toFixed(3)}</div></div>
      </div>

      <label className="field-label">Issue directive</label>
      <select className="ship-select" style={{ width: "100%" }} value={action} onChange={(e) => setAction(e.target.value)}>
        <option value="reroute_to_port">Reroute to port</option>
        <option value="divert_to_waypoint">Divert to waypoint</option>
        <option value="hold_position">Hold position</option>
      </select>

      {action === "reroute_to_port" && (
        <select className="ship-select" style={{ width: "100%", marginTop: 8 }} value={targetPort} onChange={(e) => setTargetPort(e.target.value)}>
          <option value="">Select port…</option>
          {Object.entries(ports).map(([id, name]) => <option key={id} value={id}>{name} ({id})</option>)}
        </select>
      )}
      {action === "divert_to_waypoint" && (
        <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
          <input type="text" placeholder="lat" value={wpLat} onChange={(e) => setWpLat(e.target.value)} />
          <input type="text" placeholder="lng" value={wpLng} onChange={(e) => setWpLng(e.target.value)} />
        </div>
      )}

      <div className="btn-row">
        <button className="btn primary" disabled={busy || (action === "reroute_to_port" && !targetPort)} onClick={issueDirective}>
          Send directive to captain
        </button>
        <button className="btn" disabled={busy} onClick={loadRouteOptions}>Show route options</button>
      </div>
      {err && <div style={{ color: "var(--critical-red)", fontSize: 11.5, marginTop: 6 }}>{err}</div>}

      {routeOptions && (
        <div style={{ marginTop: 12 }}>
          <label className="field-label">Candidate routes</label>
          {routeOptions.map((opt) => (
            <div className="route-option" key={opt.bias}>
              <div>
                <div className="label">{opt.label}</div>
                <div className="stats">{opt.distance_km} km · ~{opt.estimated_fuel_tons} t fuel ({opt.estimated_fuel_percent}% of initial)</div>
              </div>
              <button className="btn" onClick={() => useRoute(opt)}>Use</button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
