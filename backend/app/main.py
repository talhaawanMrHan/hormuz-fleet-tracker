"""
main.py — FastAPI application. WebSocket support is native (Starlette),
so there's no Socket.IO / Flask-SocketIO-style middleware layer to install
or configure: the REST routes and the /ws route live in the same app,
same process.

Role enforcement note (documented assumption, per the brief's ground
rules): this build has no user-account system — a real deployment would
put proper auth in front of this. Role is asserted via an `X-Role` header
on REST calls and a `role` query param on the WebSocket connection. Command-
only actions check for `X-Role: command`.
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager
from typing import List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import geo
from .models import (
    AssistanceRequestType, CaptainResponse, DirectiveAction, ShipStatus, ZoneKind,
)
from .simulator import FleetSimulator
from .storage import FleetStorage
from .ws import manager

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("main")

DB_PATH = os.environ.get("DB_PATH", "/data/fleet.db")
FLEET_JSON_PATH = os.path.join(os.path.dirname(__file__), "..", "fleet.json")

storage = FleetStorage(DB_PATH)
simulator = FleetSimulator(manager, storage)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await storage.connect()
    with open(FLEET_JSON_PATH) as f:
        fleet_data = json.load(f)
    simulator.load_fleet(fleet_data)
    simulator.start()
    log.info("fleet command backend ready — %d ships loaded", len(simulator.ships))
    yield
    await simulator.stop()
    await storage.close()


app = FastAPI(title="Fleet Command — Strait of Hormuz", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # hackathon/demo scope — lock this down for real deployment
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def require_command(x_role: str = Header(default="captain")):
    if x_role != "command":
        raise HTTPException(status_code=403, detail="Command role required for this action")
    return True


def require_captain_ship(
    x_role: str = Header(default=""),
    x_ship_id: str = Header(default=""),
):
    if x_role != "captain" or not x_ship_id:
        raise HTTPException(status_code=403, detail="Captain role and X-Ship-ID are required")
    return x_ship_id


# ---------------------------------------------------------------------------
# Static/reference data
# ---------------------------------------------------------------------------

@app.get("/api/health")
async def health():
    return {"status": "ok", "ships": len(simulator.ships), "connected_clients": manager.count()}


@app.get("/api/bbox")
async def get_bbox():
    return {"bbox": geo.BBOX, "navigable_water": geo.NAVIGABLE_WATER}


@app.get("/api/ports")
async def get_ports():
    # Keep the canonical scenario IDs for directive payloads, but expose the
    # human-readable port name in the selector.
    return geo.PORT_NAMES


# ---------------------------------------------------------------------------
# Fleet / zones / alerts (read)
# ---------------------------------------------------------------------------

@app.get("/api/fleet")
async def get_fleet():
    return {"ships": [s.model_dump() for s in simulator.ships.values()]}


@app.get("/api/zones")
async def get_zones():
    return {"zones": [z.model_dump() for z in simulator.zones.values()]}


@app.get("/api/alerts")
async def get_alerts(limit: int = 200):
    return {"alerts": await storage.recent_alerts(limit)}


# ---------------------------------------------------------------------------
# Zones (Command only)
# ---------------------------------------------------------------------------

class ZoneCreateRequest(BaseModel):
    name: str
    polygon: List[List[float]]
    kind: ZoneKind = ZoneKind.RESTRICTED


@app.post("/api/zones")
async def create_zone(req: ZoneCreateRequest, _: bool = Depends(require_command)):
    if len(req.polygon) < 3:
        raise HTTPException(status_code=400, detail="A zone polygon needs at least 3 points")
    zone = await simulator.add_zone(req.name, [tuple(p) for p in req.polygon], req.kind)
    return zone.model_dump()


@app.put("/api/zones/{zone_id}")
async def update_zone(zone_id: str, req: ZoneCreateRequest, _: bool = Depends(require_command)):
    if len(req.polygon) < 3:
        raise HTTPException(status_code=400, detail="A zone polygon needs at least 3 points")
    try:
        zone = await simulator.update_zone(zone_id, [tuple(p) for p in req.polygon])
    except ValueError as exc:
        raise HTTPException(status_code=404 if "not found" in str(exc).lower() else 400, detail=str(exc))
    return zone.model_dump()


@app.delete("/api/zones/{zone_id}")
async def delete_zone(zone_id: str, _: bool = Depends(require_command)):
    if zone_id not in simulator.zones:
        raise HTTPException(status_code=404, detail="Zone not found")
    await simulator.delete_zone(zone_id)
    return {"deleted": zone_id}


# ---------------------------------------------------------------------------
# Alerts (ack/resolve — either role can acknowledge what they see)
# ---------------------------------------------------------------------------

@app.post("/api/alerts/{alert_id}/ack")
async def ack_alert(alert_id: str):
    if alert_id not in simulator.alerts:
        raise HTTPException(status_code=404, detail="Alert not found")
    await simulator.acknowledge_alert(alert_id)
    return {"acknowledged": alert_id}


@app.post("/api/alerts/{alert_id}/resolve")
async def resolve_alert(alert_id: str):
    if alert_id not in simulator.alerts:
        raise HTTPException(status_code=404, detail="Alert not found")
    await simulator.resolve_alert(alert_id)
    return {"resolved": alert_id}


# ---------------------------------------------------------------------------
# Directives (Command issues, Captain responds)
# ---------------------------------------------------------------------------

class DirectiveCreateRequest(BaseModel):
    ship_id: str
    action: DirectiveAction
    target_port: Optional[str] = None
    waypoint: Optional[List[float]] = None


@app.post("/api/directives")
async def create_directive(req: DirectiveCreateRequest, _: bool = Depends(require_command)):
    if req.ship_id not in simulator.ships:
        raise HTTPException(status_code=404, detail="Ship not found")
    if req.action == DirectiveAction.REROUTE_TO_PORT and req.target_port not in geo.PORTS:
        raise HTTPException(status_code=400, detail="Unknown target_port")
    waypoint = tuple(req.waypoint) if req.waypoint else None
    directive = await simulator.issue_directive(req.ship_id, req.action, req.target_port, waypoint)
    return directive.model_dump()


@app.post("/api/captain-response")
async def captain_response(resp: CaptainResponse, captain_ship_id: str = Depends(require_captain_ship)):
    if resp.ship_id != captain_ship_id:
        raise HTTPException(status_code=403, detail="Captain is scoped to a single ship")
    if resp.ship_id not in simulator.ships:
        raise HTTPException(status_code=404, detail="Ship not found")
    try:
        return await simulator.apply_captain_response(resp)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ---------------------------------------------------------------------------
# Distress (standalone, not tied to a directive)
# ---------------------------------------------------------------------------

class DistressRequest(BaseModel):
    ship_id: str
    message: str


@app.post("/api/distress")
async def report_distress(req: DistressRequest, captain_ship_id: str = Depends(require_captain_ship)):
    if req.ship_id != captain_ship_id:
        raise HTTPException(status_code=403, detail="Captain is scoped to a single ship")
    if req.ship_id not in simulator.ships:
        raise HTTPException(status_code=404, detail="Ship not found")
    report = await simulator.create_distress(req.ship_id, req.message)
    return report.model_dump()


# ---------------------------------------------------------------------------
# Multi-route candidates (bonus)
# ---------------------------------------------------------------------------

@app.get("/api/ships/{ship_id}/route-options")
async def get_route_options(ship_id: str, _: bool = Depends(require_command)):
    if ship_id not in simulator.ships:
        raise HTTPException(status_code=404, detail="Ship not found")
    return {"options": simulator.route_options(ship_id)}


class RouteSelectRequest(BaseModel):
    ship_id: str
    waypoints: List[List[float]]


@app.post("/api/ships/route-select")
async def select_route(req: RouteSelectRequest, _: bool = Depends(require_command)):
    if req.ship_id not in simulator.ships:
        raise HTTPException(status_code=404, detail="Ship not found")
    await simulator.select_route(req.ship_id, [tuple(p) for p in req.waypoints])
    return {"ok": True}


# ---------------------------------------------------------------------------
# Ship-to-ship assistance (bonus)
# ---------------------------------------------------------------------------

class AssistanceRequestBody(BaseModel):
    ship_id: str
    kind: AssistanceRequestType


@app.post("/api/assistance")
async def request_assistance(req: AssistanceRequestBody, captain_ship_id: str = Depends(require_captain_ship)):
    if req.ship_id != captain_ship_id:
        raise HTTPException(status_code=403, detail="Captain is scoped to a single ship")
    if req.ship_id not in simulator.ships:
        raise HTTPException(status_code=404, detail="Ship not found")
    result = await simulator.request_assistance(req.ship_id, req.kind)
    return result.model_dump()


class AssistanceResponseBody(BaseModel):
    request_id: str
    responding_ship_id: str
    accept: bool


@app.post("/api/assistance/respond")
async def respond_assistance(req: AssistanceResponseBody):
    try:
        result = await simulator.respond_assistance(req.request_id, req.responding_ship_id, req.accept)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return result.model_dump()


# ---------------------------------------------------------------------------
# AI fleet advisor (bonus, on-demand)
# ---------------------------------------------------------------------------

@app.post("/api/advisor")
async def run_advisor(_: bool = Depends(require_command)):
    return await simulator.advisor_suggestions()


# ---------------------------------------------------------------------------
# Playback / history
# ---------------------------------------------------------------------------

@app.get("/api/history/timeline")
async def history_timeline():
    return {"timestamps_ms": await storage.timeline()}


@app.get("/api/history/snapshot")
async def history_snapshot(ts: int = Query(..., description="epoch ms")):
    snap = await storage.snapshot_at_or_before(ts)
    if snap is None:
        raise HTTPException(status_code=404, detail="No snapshot at or before that time yet")
    return snap


# ---------------------------------------------------------------------------
# WebSocket — every connected client gets the same broadcast fleet state
# ---------------------------------------------------------------------------

@app.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    role: str = Query(default="captain"),
    ship_id: Optional[str] = Query(default=None),
):
    await manager.connect(websocket, role=role, ship_id=ship_id)
    try:
        await manager.send_to(websocket, {
            "type": "fleet_state", "tick": 0, "ships": [s.model_dump() for s in simulator.ships.values()],
        })
        await manager.send_to(websocket, {"type": "zones_snapshot", "zones": [z.model_dump() for z in simulator.zones.values()]})
        while True:
            # Clients don't need to send anything for the simulation to run;
            # this just keeps the connection alive and detects disconnects.
            await websocket.receive_text()
    except WebSocketDisconnect:
        await manager.disconnect(websocket)
    except Exception:  # noqa: BLE001
        await manager.disconnect(websocket)
