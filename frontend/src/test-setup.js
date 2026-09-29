import "@testing-library/jest-dom";

const SHIP = {
  id: "ship_01", name: "MV Test Ship", imo: "9123456", position: [56.1, 26.5],
  heading: 90, speed_knots: 12, max_speed_knots: 18, destination_port: "MCT-1",
  fuel_percent: 80, fuel_tons: 5440, initial_fuel_tons: 6800, fuel_burn_per_km: 0.0, fuel_burn_tons_per_km: 0.115, cargo: "container", cargo_detail: "test cargo",
  status: "normal", fuel_warning: false, route: [[56.1, 26.5], [56.2, 26.6]], route_index: 0,
  distance_remaining_km: 40, in_adverse_weather: false, last_directive: null, updated_at: Date.now(),
};

class MockWebSocket {
  static OPEN = 1;
  static CLOSED = 3;
  constructor(url) {
    this.url = url;
    this.readyState = MockWebSocket.OPEN;
    // Mirror the real backend (see main.py websocket_endpoint), which
    // pushes an immediate fleet_state + zones_snapshot right after accept.
    setTimeout(() => {
      this.onopen && this.onopen({});
      this.onmessage && this.onmessage({ data: JSON.stringify({ type: "fleet_state", tick: 0, ships: [SHIP] }) });
      this.onmessage && this.onmessage({ data: JSON.stringify({ type: "zones_snapshot", zones: [] }) });
    }, 0);
  }
  send() {}
  close() {
    this.readyState = MockWebSocket.CLOSED;
    this.onclose && this.onclose({});
  }
}
global.WebSocket = MockWebSocket;

if (!global.ResizeObserver) {
  global.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
}

// jsdom doesn't implement SVG layout/measurement APIs that Leaflet's vector
// (Polygon/Path) renderer relies on — stub the ones it touches so map
// components can mount in a non-browser test environment.
if (typeof SVGElement !== "undefined") {
  if (!SVGElement.prototype.getBBox) {
    SVGElement.prototype.getBBox = () => ({ x: 0, y: 0, width: 0, height: 0 });
  }
  if (!SVGElement.prototype.getCTM) {
    SVGElement.prototype.getCTM = () => null;
  }
  if (!SVGElement.prototype.getScreenCTM) {
    SVGElement.prototype.getScreenCTM = () => null;
  }
}

const ROUTES = {
  "/api/bbox": { bbox: { min_lng: 47.5, max_lng: 60.0, min_lat: 22.0, max_lat: 30.5 }, navigable_water: [[48.6, 29.8], [56.1, 26.65], [60.0, 25.0], [60.0, 22.0], [48.3, 29.5]] },
  "/api/ports": { "MCT-1": "Muscat", "DXB-1": "Jebel Ali", "BND-1": "Bandar Abbas" },
  "/api/alerts?limit=200": { alerts: [] },
  "/api/zones": { zones: [] },
  "/api/history/timeline": { timestamps_ms: [] },
  "/api/fleet": { ships: [SHIP] },
};

global.fetch = async (url) => {
  const path = url.replace(/^https?:\/\/[^/]+/, "");
  const match = Object.keys(ROUTES).find((k) => path === k || path.startsWith(k.split("?")[0]));
  const body = match ? ROUTES[match] : {};
  return {
    ok: true,
    status: 200,
    json: async () => body,
  };
};

export { SHIP };
