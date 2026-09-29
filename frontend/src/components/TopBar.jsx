import React from "react";

export default function TopBar({ role, setRole, connected, ships, captainShipId, setCaptainShipId, clientCount }) {
  return (
    <div className="topbar">
      <div className="brand">
        <span className="dot" />
        Fleet Command — Strait of Hormuz
      </div>

      <div className="conn-status">
        <span className={`conn-dot ${connected ? "live" : "down"}`} />
        {connected ? "Connected" : "Reconnecting…"}
      </div>

      {role === "captain" && (
        <select
          className="ship-select"
          value={captainShipId || ""}
          onChange={(e) => setCaptainShipId(e.target.value)}
        >
          <option value="" disabled>Select your ship…</option>
          {[...ships].sort((a, b) => a.name.localeCompare(b.name)).map((s) => (
            <option key={s.id} value={s.id}>{s.name}</option>
          ))}
        </select>
      )}

      <div className="role-switch">
        <button className={role === "command" ? "active" : ""} onClick={() => setRole("command")}>Command</button>
        <button className={role === "captain" ? "active" : ""} onClick={() => setRole("captain")}>Captain</button>
      </div>
    </div>
  );
}
