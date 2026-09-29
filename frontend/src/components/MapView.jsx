import React, { useEffect, useMemo, useRef } from "react";
import { MapContainer, TileLayer, Polygon, Marker, Popup, useMap } from "react-leaflet";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import "leaflet-draw/dist/leaflet.draw.css";
import "leaflet-draw";

const STATUS_COLOR = {
  normal: "#3ecf8e",
  rerouting: "#e8a33d",
  distressed: "#e5555c",
  stranded: "#e5555c",
  insufficient_fuel: "#e5555c",
  stopped: "#7d97a8",
  arrived: "#6fa8dc",
};

function toLatLngs(coords) {
  // our coords are [lng, lat]; Leaflet wants [lat, lng]
  return coords.map(([lng, lat]) => [lat, lng]);
}

function shipIcon(ship, isSelected) {
  const color = STATUS_COLOR[ship.status] || "#dfeaf0";
  const heading = ship.heading || 0;
  const size = isSelected ? 22 : 16;
  const html = `
    <div style="transform: rotate(${heading}deg); width:${size}px; height:${size}px;">
      <svg viewBox="0 0 24 24" width="${size}" height="${size}">
        <path d="M12 2 L20 20 L12 16 L4 20 Z" fill="${color}" stroke="#071019" stroke-width="1.2"/>
      </svg>
    </div>
    <div class="ship-marker-label" style="margin-top:2px; ${isSelected ? "border-color:" + color + ";" : ""}">${ship.name}</div>
  `;
  return L.divIcon({
    html: `<div style="display:flex; flex-direction:column; align-items:center;">${html}</div>`,
    className: "",
    iconSize: [size, size],
    iconAnchor: [size / 2, size / 2],
  });
}

function DrawControl({ enabled, onCreate }) {
  const map = useMap();
  const drawnItemsRef = useRef(null);
  const controlRef = useRef(null);

  useEffect(() => {
    if (!enabled) return undefined;

    const drawnItems = new L.FeatureGroup();
    map.addLayer(drawnItems);
    drawnItemsRef.current = drawnItems;

    const control = new L.Control.Draw({
      draw: {
        polygon: {
          shapeOptions: { color: "#e8a33d", weight: 2, fillOpacity: 0.12 },
          allowIntersection: false,
          showArea: false,
        },
        polyline: false, rectangle: false, circle: false, marker: false, circlemarker: false,
      },
      edit: { featureGroup: drawnItems, remove: true },
    });
    map.addControl(control);
    controlRef.current = control;

    function handleCreated(e) {
      const latlngs = e.layer.getLatLngs()[0];
      const coords = latlngs.map((p) => [p.lng, p.lat]); // back to [lng, lat] for the API
      drawnItems.removeLayer(e.layer); // we render committed zones ourselves via props
      onCreate(coords);
    }
    map.on(L.Draw.Event.CREATED, handleCreated);

    return () => {
      map.off(L.Draw.Event.CREATED, handleCreated);
      map.removeControl(control);
      map.removeLayer(drawnItems);
    };
  }, [enabled, map, onCreate]);

  return null;
}

function ZoneEditControl({ enabled, zones, onEdit }) {
  const map = useMap();
  const groupRef = useRef(null);
  const controlRef = useRef(null);

  useEffect(() => {
    if (!enabled) return undefined;
    const group = new L.FeatureGroup();
    map.addLayer(group);
    groupRef.current = group;

    for (const z of zones) {
      const layer = L.polygon(toLatLngs(z.polygon), { opacity: 0, fillOpacity: 0, zoneId: z.id });
      layer.options.zoneId = z.id;
      group.addLayer(layer);
    }

    const control = new L.Control.Draw({
      draw: false,
      edit: { featureGroup: group, remove: false },
    });
    map.addControl(control);
    controlRef.current = control;

    function handleEdited(e) {
      e.layers.eachLayer((layer) => {
        const zoneId = layer.options.zoneId;
        const latlngs = layer.getLatLngs()[0];
        if (!zoneId || !latlngs || latlngs.length < 3) return;
        onEdit(zoneId, latlngs.map((p) => [p.lng, p.lat]));
      });
    }
    map.on(L.Draw.Event.EDITED, handleEdited);

    return () => {
      map.off(L.Draw.Event.EDITED, handleEdited);
      if (controlRef.current) map.removeControl(controlRef.current);
      map.removeLayer(group);
    };
  }, [enabled, zones, map, onEdit]);

  return null;
}

function FocusOnShip({ ship }) {
  const map = useMap();
  useEffect(() => {
    if (ship) map.setView([ship.position[1], ship.position[0]], Math.max(map.getZoom(), 8), { animate: true });
  }, [ship?.id]); // eslint-disable-line react-hooks/exhaustive-deps
  return null;
}

export default function MapView({
  ships, zones, navigableWater, bbox, role, selectedShipId, onSelectShip, onZoneCreate, onZoneEdit, onZoneDelete, focusShip,
}) {
  const center = useMemo(() => {
    if (!bbox) return [26.0, 56.0];
    return [(bbox.min_lat + bbox.max_lat) / 2, (bbox.min_lng + bbox.max_lng) / 2];
  }, [bbox]);

  return (
    <MapContainer center={center} zoom={7} zoomControl={true} attributionControl={true}>
      <TileLayer
        url="https://tile.openstreetmap.org/{z}/{x}/{y}.png"
        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
        maxZoom={19}
      />
      {navigableWater && (
        <Polygon
          positions={toLatLngs(navigableWater)}
          pathOptions={{ color: "#2c6e7c", weight: 1, fillOpacity: 0.03, dashArray: "3,5" }}
        />
      )}
      {zones.map((z) => (
        <Polygon
          key={z.id}
          positions={toLatLngs(z.polygon)}
          pathOptions={{ color: z.kind === "storm" ? "#6fa8dc" : "#e8a33d", weight: 2, fillOpacity: 0.15 }}
        >
          <Popup>
            <div style={{ fontSize: 12 }}>
              <strong>{z.name}</strong>
              <div>{z.kind}</div>
              {role === "command" && (
                <button className="btn danger" style={{ marginTop: 6 }} onClick={() => onZoneDelete(z.id)}>
                  Delete zone
                </button>
              )}
            </div>
          </Popup>
        </Polygon>
      ))}
      {ships.map((s) => (
        <Marker
          key={s.id}
          position={[s.position[1], s.position[0]]}
          icon={shipIcon(s, s.id === selectedShipId)}
          eventHandlers={{ click: () => onSelectShip(s.id) }}
        />
      ))}
      <DrawControl enabled={role === "command"} onCreate={onZoneCreate} />
      <ZoneEditControl enabled={role === "command"} zones={zones} onEdit={onZoneEdit} />
      <FocusOnShip ship={focusShip} />
    </MapContainer>
  );
}
