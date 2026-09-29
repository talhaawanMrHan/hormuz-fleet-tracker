import React, { useCallback, useEffect, useMemo, useState } from "react";
import TopBar from "./components/TopBar.jsx";
import MapView from "./components/MapView.jsx";
import ShipList from "./components/ShipList.jsx";
import CommandDetailPanel from "./components/CommandDetailPanel.jsx";
import CaptainPanel from "./components/CaptainPanel.jsx";
import AlertFeed from "./components/AlertFeed.jsx";
import AdvisorPanel from "./components/AdvisorPanel.jsx";
import Timeline from "./components/Timeline.jsx";
import { api } from "./api/client";
import { useFleetSocket } from "./api/useFleetSocket";

export default function App() {
  const [role, setRole] = useState("command");
  const [captainShipId, setCaptainShipId] = useState("");
  const [selectedShipId, setSelectedShipId] = useState(null);
  const [bbox, setBbox] = useState(null);
  const [navigableWater, setNavigableWater] = useState(null);
  const [ports, setPorts] = useState({});
  const [playbackShips, setPlaybackShips] = useState(null);

  const {
    connected, ships, zones, alerts, directives, advisorSuggestions,
    incidentReports, refreshAlerts,
  } = useFleetSocket(role, role === "captain" ? captainShipId || null : null);

  useEffect(() => {
    api.getBbox().then((d) => { setBbox(d.bbox); setNavigableWater(d.navigable_water); }).catch(() => {});
    api.getPorts().then(setPorts).catch(() => {});
  }, []);

  useEffect(() => {
    if (role === "captain" && !captainShipId && ships.length > 0) {
      setCaptainShipId(ships[0].id);
    }
  }, [role, ships, captainShipId]);

  const displayShips = playbackShips || ships;

  const selectedShip = useMemo(
    () => displayShips.find((s) => s.id === selectedShipId) || null,
    [displayShips, selectedShipId]
  );
  const captainShip = useMemo(
    () => displayShips.find((s) => s.id === captainShipId) || null,
    [displayShips, captainShipId]
  );

  const handleZoneCreate = useCallback((coords) => {
    const name = window.prompt("Name this restricted zone:", "Restricted Zone") || "Restricted Zone";
    api.createZone(name, coords).catch((e) => alert(`Could not create zone: ${e.message}`));
  }, []);

  const handleZoneEdit = useCallback((zoneId, polygon) => {
    const zone = zones.find((z) => z.id === zoneId);
    if (!zone) return;
    api.updateZone(zoneId, zone.name, polygon, zone.kind).catch((e) => alert(`Could not update zone: ${e.message}`));
  }, [zones]);

  const handleZoneDelete = useCallback((zoneId) => {
    api.deleteZone(zoneId).catch((e) => alert(`Could not delete zone: ${e.message}`));
  }, []);

  return (
    <div className="app-shell">
      <TopBar
        role={role} setRole={setRole} connected={connected} ships={ships}
        captainShipId={captainShipId} setCaptainShipId={setCaptainShipId}
      />
      <div className="main-body">
        <div className="map-col">
          <MapView
            ships={displayShips}
            zones={zones}
            navigableWater={navigableWater}
            bbox={bbox}
            role={role}
            selectedShipId={role === "command" ? selectedShipId : captainShipId}
            onSelectShip={role === "command" ? setSelectedShipId : () => {}}
            onZoneCreate={handleZoneCreate}
            onZoneEdit={handleZoneEdit}
            onZoneDelete={handleZoneDelete}
            focusShip={role === "captain" ? captainShip : null}
          />
          {playbackShips && (
            <div className="map-toolbar">
              <button className="active">⏵ Playback mode — timeline below</button>
            </div>
          )}
        </div>

        <div className="sidebar">
          {role === "command" ? (
            <>
              <div className="sidebar-section" style={{ maxHeight: 220 }}>
                <h3>Fleet ({ships.length})</h3>
                <div className="scroll scrollthin" style={{ flex: 1 }}>
                  <ShipList ships={displayShips} selectedShipId={selectedShipId} onSelect={setSelectedShipId} />
                </div>
              </div>
              <div className="sidebar-section" style={{ maxHeight: 320 }}>
                <h3>Ship detail &amp; directives</h3>
                <div className="scroll scrollthin" style={{ flex: 1 }}>
                  <CommandDetailPanel ship={selectedShip} ports={ports} onChanged={refreshAlerts} />
                </div>
              </div>
              <div className="sidebar-section grow">
                <h3>AI fleet advisor</h3>
                <AdvisorPanel suggestions={advisorSuggestions} onRefresh={refreshAlerts} />
                <h3 style={{ marginTop: 4 }}>Alerts</h3>
                <div className="scroll scrollthin" style={{ flex: 1 }}>
                  <AlertFeed alerts={alerts} ships={displayShips} onChanged={refreshAlerts} incidentReports={incidentReports} />
                </div>
              </div>
            </>
          ) : (
            <>
              <div className="sidebar-section" style={{ maxHeight: 480 }}>
                <div className="scroll scrollthin" style={{ flex: 1 }}>
                  <CaptainPanel ship={captainShip} directives={directives} onChanged={refreshAlerts} ports={ports} />
                </div>
              </div>
              <div className="sidebar-section grow">
                <h3>Alerts</h3>
                <div className="scroll scrollthin" style={{ flex: 1 }}>
                  <AlertFeed alerts={alerts} ships={displayShips} onChanged={refreshAlerts} incidentReports={incidentReports} />
                </div>
              </div>
            </>
          )}
        </div>
      </div>
      <Timeline onPlaybackChange={setPlaybackShips} />
    </div>
  );
}
