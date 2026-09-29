const BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";

async function req(path, options = {}) {
  const resp = await fetch(`${BASE}${path}`, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  if (!resp.ok) {
    let detail = resp.statusText || "Request failed";
    try {
      const body = await resp.json();
      if (Array.isArray(body.detail)) {
        detail = body.detail.map((item) => {
          const location = Array.isArray(item.loc) ? item.loc.join(".") : "";
          return `${location ? `${location}: ` : ""}${item.msg || "Validation error"}`;
        }).join("; ");
      } else if (typeof body.detail === "object" && body.detail !== null) {
        detail = JSON.stringify(body.detail);
      } else if (body.detail) {
        detail = body.detail;
      }
    } catch (_) {
      /* ignore non-JSON error bodies */
    }
    throw new Error(`${resp.status}: ${detail}`);
  }
  if (resp.status === 204) return null;
  return resp.json();
}

const commandHeaders = { "X-Role": "command" };

export const api = {
  wsBase: (import.meta.env.VITE_WS_BASE || BASE.replace(/^http/, "ws")),

  getFleet: () => req("/api/fleet"),
  getZones: () => req("/api/zones"),
  getAlerts: (limit = 200) => req(`/api/alerts?limit=${limit}`),
  getBbox: () => req("/api/bbox"),
  getPorts: () => req("/api/ports"),

  createZone: (name, polygon, kind = "restricted") =>
    req("/api/zones", { method: "POST", headers: commandHeaders, body: JSON.stringify({ name, polygon, kind }) }),
  updateZone: (zoneId, name, polygon, kind = "restricted") =>
    req(`/api/zones/${zoneId}`, { method: "PUT", headers: commandHeaders, body: JSON.stringify({ name, polygon, kind }) }),
  deleteZone: (zoneId) => req(`/api/zones/${zoneId}`, { method: "DELETE", headers: commandHeaders }),

  ackAlert: (alertId) => req(`/api/alerts/${alertId}/ack`, { method: "POST" }),
  resolveAlert: (alertId) => req(`/api/alerts/${alertId}/resolve`, { method: "POST" }),

  createDirective: (shipId, action, targetPort, waypoint) =>
    req("/api/directives", {
      method: "POST",
      headers: commandHeaders,
      body: JSON.stringify({
        ship_id: shipId,
        action,
        ...(targetPort ? { target_port: targetPort } : {}),
        ...(waypoint ? { waypoint } : {}),
      }),
    }),

  captainRespond: (directiveId, shipId, response, distressMessage) =>
    req("/api/captain-response", {
      method: "POST",
      headers: { "X-Role": "captain", "X-Ship-ID": shipId },
      body: JSON.stringify({
        directive_id: directiveId, ship_id: shipId, response, distress_message: distressMessage,
      }),
    }),

  reportDistress: (shipId, message) =>
    req("/api/distress", {
      method: "POST", headers: { "X-Role": "captain", "X-Ship-ID": shipId },
      body: JSON.stringify({ ship_id: shipId, message }),
    }),

  getRouteOptions: (shipId) => req(`/api/ships/${shipId}/route-options`, { headers: commandHeaders }),
  selectRoute: (shipId, waypoints) =>
    req("/api/ships/route-select", {
      method: "POST", headers: commandHeaders,
      body: JSON.stringify({ ship_id: shipId, waypoints }),
    }),

  requestAssistance: (shipId, kind) =>
    req("/api/assistance", {
      method: "POST", headers: { "X-Role": "captain", "X-Ship-ID": shipId },
      body: JSON.stringify({ ship_id: shipId, kind }),
    }),
  respondAssistance: (requestId, respondingShipId, accept) =>
    req("/api/assistance/respond", {
      method: "POST",
      body: JSON.stringify({ request_id: requestId, responding_ship_id: respondingShipId, accept }),
    }),

  runAdvisor: () => req("/api/advisor", { method: "POST", headers: commandHeaders }),

  getTimeline: () => req("/api/history/timeline"),
  getSnapshot: (ts) => req(`/api/history/snapshot?ts=${ts}`),
};
