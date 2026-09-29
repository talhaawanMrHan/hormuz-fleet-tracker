import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./client";

const TICK_INTERVAL_MS = 1000;
const INTERPOLATION_FPS = 15;

function lerp(a, b, t) {
  return a + (b - a) * t;
}
function lerpPos(a, b, t) {
  return [lerp(a[0], b[0], t), lerp(a[1], b[1], t)];
}

export function useFleetSocket(role, shipId) {
  const [connected, setConnected] = useState(false);
  const [ships, setShips] = useState([]);
  const [zones, setZones] = useState([]);
  const [alerts, setAlerts] = useState([]);
  const [directives, setDirectives] = useState({});
  const [advisorSuggestions, setAdvisorSuggestions] = useState(null);
  const [incidentReports, setIncidentReports] = useState({});
  const [assistanceRequests, setAssistanceRequests] = useState({});

  const wsRef = useRef(null);
  const prevShipsRef = useRef(new Map());
  const currShipsRef = useRef(new Map());
  const tickTimeRef = useRef(Date.now());

  useEffect(() => {
    api.getAlerts().then((d) => setAlerts(d.alerts)).catch(() => {});
    api.getZones().then((d) => setZones(d.zones)).catch(() => {});
  }, []);

  useEffect(() => {
    let stopped = false;
    let ws;

    function connect() {
      if (stopped) return;
      const url = `${api.wsBase}/ws?role=${role}${shipId ? `&ship_id=${shipId}` : ""}`;
      ws = new WebSocket(url);
      wsRef.current = ws;

      ws.onopen = () => setConnected(true);
      ws.onclose = () => {
        setConnected(false);
        if (!stopped) setTimeout(connect, 1500);
      };
      ws.onerror = () => ws.close();

      ws.onmessage = (evt) => {
        const msg = JSON.parse(evt.data);
        switch (msg.type) {
          case "fleet_state": {
            prevShipsRef.current = currShipsRef.current;
            const m = new Map();
            for (const s of msg.ships) m.set(s.id, s);
            currShipsRef.current = m;
            tickTimeRef.current = Date.now();
            break;
          }
          case "zones_snapshot":
            setZones(msg.zones);
            break;
          case "zone_created":
            setZones((z) => [...z.filter((x) => x.id !== msg.zone.id), msg.zone]);
            break;
          case "zone_updated":
            setZones((z) => z.map((x) => (x.id === msg.zone.id ? msg.zone : x)));
            break;
          case "zone_deleted":
            setZones((z) => z.filter((x) => x.id !== msg.zone_id));
            break;
          case "alert":
            setAlerts((a) => [msg.alert, ...a]);
            break;
          case "alert_update":
            setAlerts((a) => a.map((x) => (x.id === msg.alert.id ? msg.alert : x)));
            break;
          case "directive_issued":
          case "directive_resolved":
            setDirectives((d) => ({ ...d, [msg.directive.id]: msg.directive }));
            if (msg.ship) currShipsRef.current.set(msg.ship.id, msg.ship);
            break;
          case "assistance_request":
            setAssistanceRequests((r) => ({
              ...r,
              [msg.request.id]: { ...msg.request, nearby_ship_ids: msg.nearby_ship_ids },
            }));
            break;
          case "assistance_response":
            setAssistanceRequests((r) => ({ ...r, [msg.request.id]: msg.request }));
            break;
          case "advisor_suggestions":
            setAdvisorSuggestions(msg);
            break;
          case "incident_report":
            setIncidentReports((r) => ({ ...r, [msg.alert_id]: msg.report }));
            break;
          case "ship_update":
            currShipsRef.current.set(msg.ship.id, msg.ship);
            break;
          default:
            break;
        }
      };
    }

    connect();
    return () => {
      stopped = true;
      wsRef.current?.close();
    };
  }, [role, shipId]);

  // Client-side interpolation: repaint ship positions ~15x/sec, lerping
  // between the previous and current server ticks, instead of only
  // updating on every 1Hz server message (which would look like teleporting).
  useEffect(() => {
    const interval = setInterval(() => {
      const now = Date.now();
      const t = Math.min(1, (now - tickTimeRef.current) / TICK_INTERVAL_MS);
      const out = [];
      for (const [id, curr] of currShipsRef.current.entries()) {
        const prev = prevShipsRef.current.get(id) || curr;
        out.push({ ...curr, position: lerpPos(prev.position, curr.position, t) });
      }
      setShips(out);
    }, 1000 / INTERPOLATION_FPS);
    return () => clearInterval(interval);
  }, []);

  const refreshAlerts = useCallback(() => {
    api.getAlerts().then((d) => setAlerts(d.alerts)).catch(() => {});
  }, []);

  return {
    connected, ships, zones, alerts, directives, advisorSuggestions,
    incidentReports, assistanceRequests, refreshAlerts,
  };
}
