import React from "react";

export default function ShipList({ ships, selectedShipId, onSelect }) {
  const sorted = [...ships].sort((a, b) => a.name.localeCompare(b.name));
  return (
    <div>
      {sorted.map((s) => (
        <div
          key={s.id}
          className={`ship-row ${s.id === selectedShipId ? "selected" : ""}`}
          onClick={() => onSelect(s.id)}
        >
          <span className={`status-chip status-${s.status}`}>{s.status.replace("_", " ")}</span>
          <span className="name">{s.name}</span>
          <span className="fuel">{Math.round(s.fuel_tons)}t</span>
        </div>
      ))}
    </div>
  );
}
