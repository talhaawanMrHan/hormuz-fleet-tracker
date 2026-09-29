"""
simulator.py — the heart of the system. Owns all live ship/zone/alert state
in memory and advances it once per tick. Nothing here talks to a client
directly; main.py wires this to WebSocket/REST.

State-transition philosophy (documented, since the brief asks us to
document assumptions): `status` is event-driven, not recomputed from
scratch every tick — it flips in response to specific triggers exactly as
the brief describes ("status flips to rerouting when..."), except for the
two checks that are inherently continuous: running out of fuel, and
arriving at the destination, which are checked every tick.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import time
from typing import Dict, List, Optional

from shapely.geometry import Point, Polygon

from . import ai, geo
from .models import (
    Alert, AlertSeverity, AlertType, AssistanceRequest, AssistanceRequestType,
    CaptainResponse, CaptainResponseType, Directive, DirectiveAction,
    DistressReport, RestrictedZone, Ship, ShipStatus, ZoneKind, new_id, now_ms,
)
from .storage import FleetStorage
from .weather import weather_service
from .ws import ConnectionManager

log = logging.getLogger("simulator")

TICK_HZ = 1.0
PROXIMITY_THRESHOLD_KM = 2.0
CPA_HORIZON_MINUTES = 12.0
CPA_WARNING_KM = 3.0          # a bit wider than the hard 2km so it's genuinely predictive
ZONE_ETA_WARNING_MINUTES = 3.0
FUEL_SAFETY_MARGIN = 1.15      # require 15% more fuel than the naive estimate before warning


class FleetSimulator:
    def __init__(self, manager: ConnectionManager, storage: FleetStorage):
        self.manager = manager
        self.storage = storage
        self.ships: Dict[str, Ship] = {}
        self.zones: Dict[str, RestrictedZone] = {}
        self.alerts: Dict[str, Alert] = {}
        self.directives: Dict[str, Directive] = {}
        self.assistance_requests: Dict[str, AssistanceRequest] = {}
        self._active_proximity_pairs: set = set()
        self._active_cpa_pairs: set = set()
        self._fired_breach_for: set = set()     # (ship_id, zone_id) currently inside, already alerted
        self._fired_zone_eta_for: set = set()   # (ship_id, zone_id) predictive warning already fired
        self._fired_fuel_warning_for: set = set()
        self._tick_task: Optional[asyncio.Task] = None
        self._tick_count = 0
        self._background_tasks: set = set()

    def _spawn(self, coro) -> asyncio.Task:
        """Fire-and-forget helper that keeps a strong reference to the task
        (a bare asyncio.create_task() result can be garbage-collected mid-
        flight, silently dropping the work) and logs any exception instead
        of letting it disappear as an unretrieved-task warning."""
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)

        def _done(t: asyncio.Task):
            self._background_tasks.discard(t)
            if not t.cancelled() and t.exception() is not None:
                log.error("background task failed: %r", t.exception())

        task.add_done_callback(_done)
        return task

    # ------------------------------------------------------------------
    # Startup
    # ------------------------------------------------------------------

    def load_fleet(self, fleet_data: dict):
        """Load the supplied fleet.json exactly as published in the brief.

        The source schema intentionally uses [lat, lng], fuel in tons, and
        compact ship/port IDs. The rest of the application uses (lng, lat)
        internally and a richer Ship model, so this is the single normalization
        boundary between the canonical scenario data and runtime state.
        """
        port_ids = {p["id"] for p in fleet_data["ports"]}
        cargo_map = {
            "containers": "container",
            "crude oil": "crude_oil",
            "LNG": "lng",
            "bulk grain": "bulk_grain",
            "automobiles": "vehicles",
            "bulk cement": "bulk_grain",
        }
        for raw in fleet_data["fleet"]:
            if raw["destination"] not in port_ids:
                raise ValueError(f"Unknown destination port in fleet.json: {raw['destination']}")
            fuel = float(raw["fuel"])
            speed = float(raw["speed"])
            # The source spec provides fuel remaining but no vessel capacity or
            # burn-rate field. We therefore preserve fuel in tons and derive a
            # conservative, speed-scaled burn rate for simulation-only fuel
            # feasibility. This assumption is documented in README.
            burn_tons_per_km = 0.08 + 0.0025 * max(speed - 10.0, 0.0)
            ship = Ship(
                id=raw["shipId"],
                name=raw["name"],
                imo=f"SCENARIO-{raw['shipId']}",
                position=(float(raw["position"][1]), float(raw["position"][0])),
                heading=float(raw["heading"]),
                speed_knots=speed,
                max_speed_knots=max(speed, 22.0),
                destination_port=raw["destination"],
                fuel_percent=100.0,
                fuel_tons=fuel,
                initial_fuel_tons=fuel,
                fuel_burn_per_km=0.0,
                fuel_burn_tons_per_km=burn_tons_per_km,
                cargo=cargo_map.get(raw["cargo"], "general"),
                cargo_detail=raw["cargo"],
                status=raw["status"],
            )
            self.ships[ship.id] = ship
        for ship in self.ships.values():
            self._compute_route(ship, reason_status=ShipStatus.NORMAL)

    def start(self):
        weather_service.start()
        if self._tick_task is None:
            self._tick_task = asyncio.create_task(self._run_forever())

    async def stop(self):
        if self._tick_task:
            self._tick_task.cancel()
        await weather_service.stop()

    # ------------------------------------------------------------------
    # Routing helpers
    # ------------------------------------------------------------------

    def _zone_polygons(self, exclude_containing: Optional[geo.Coord] = None) -> List[Polygon]:
        """All active zone polygons, optionally excluding zones that
        currently contain `exclude_containing` (used so a ship can route
        itself OUT of a zone it's already inside, instead of finding every
        neighbouring cell blocked)."""
        polys = []
        for z in self.zones.values():
            poly = Polygon(z.polygon)
            if exclude_containing is not None and poly.contains(Point(exclude_containing)):
                continue
            polys.append(poly)
        return polys

    def _compute_route(self, ship: Ship, reason_status: ShipStatus, target_port: Optional[str] = None,
                        target_waypoint: Optional[geo.Coord] = None) -> bool:
        """Recompute ship.route toward its destination (or an explicit
        waypoint/port override). Returns True on success. On failure, marks
        the ship STRANDED and fires an alert, per the brief's edge case."""
        dest_port = target_port or ship.destination_port
        goal = target_waypoint or geo.port_approach_point(dest_port)
        zones = self._zone_polygons(exclude_containing=ship.position)

        result = geo.astar_route(
            tuple(ship.position), goal, restricted_zones=zones,
            weather_penalty_fn=weather_service.routing_penalty, route_bias="balanced",
        )
        if not result.found:
            ship.status = ShipStatus.STRANDED
            ship.route = []
            self._fire_alert(
                AlertType.STRANDED, AlertSeverity.CRITICAL, [ship.id],
                f"{ship.name} has no navigable route to {dest_port} — restricted zones fully block the way.",
                {"destination_port": dest_port},
            )
            return False

        ship.route = result.waypoints
        ship.route_index = 0
        ship.distance_remaining_km = result.distance_km
        if target_port:
            ship.destination_port = target_port
        ship.status = reason_status
        self._check_fuel_projection(ship)
        return True

    def _check_fuel_projection(self, ship: Ship):
        weather_multiplier = 1.3 if ship.in_adverse_weather else 1.0
        projected_burn = (
            ship.distance_remaining_km * ship.fuel_burn_tons_per_km * weather_multiplier * FUEL_SAFETY_MARGIN
        )
        insufficient = projected_burn > ship.fuel_tons
        ship.fuel_warning = insufficient
        if insufficient and ship.status in (ShipStatus.NORMAL, ShipStatus.REROUTING):
            ship.status = ShipStatus.INSUFFICIENT_FUEL
        elif not insufficient and ship.status == ShipStatus.INSUFFICIENT_FUEL:
            ship.status = ShipStatus.REROUTING if ship.route_index < len(ship.route) - 1 else ShipStatus.ARRIVED
        if insufficient and ship.id not in self._fired_fuel_warning_for:
            self._fired_fuel_warning_for.add(ship.id)
            self._fire_alert(
                AlertType.INSUFFICIENT_FUEL, AlertSeverity.WARNING, [ship.id],
                f"{ship.name} likely doesn't have enough fuel ({ship.fuel_tons:.0f} t) to reach "
                f"{ship.destination_port} on its current route. Continuing voyage.",
                {"fuel_tons": ship.fuel_tons, "projected_burn_tons": projected_burn,
                 "distance_remaining_km": ship.distance_remaining_km},
            )
        elif not insufficient:
            self._fired_fuel_warning_for.discard(ship.id)

    # ------------------------------------------------------------------
    # Alerts
    # ------------------------------------------------------------------

    def _fire_alert(self, type_: AlertType, severity: AlertSeverity, ship_ids: List[str],
                     message: str, detail: dict) -> Alert:
        alert = Alert(type=type_, severity=severity, ship_ids=ship_ids, message=message, detail=detail)
        self.alerts[alert.id] = alert
        self._spawn(self.storage.save_alert(alert.model_dump()))
        self._spawn(self.manager.broadcast({"type": "alert", "alert": alert.model_dump()}))
        return alert

    async def acknowledge_alert(self, alert_id: str):
        if alert_id in self.alerts:
            self.alerts[alert_id].acknowledged = True
            await self.storage.update_alert(alert_id, acknowledged=True)
            await self.manager.broadcast({"type": "alert_update", "alert": self.alerts[alert_id].model_dump()})

    async def resolve_alert(self, alert_id: str):
        if alert_id in self.alerts:
            self.alerts[alert_id].resolved = True
            await self.storage.update_alert(alert_id, resolved=True)
            await self.manager.broadcast({"type": "alert_update", "alert": self.alerts[alert_id].model_dump()})

    # ------------------------------------------------------------------
    # Zones (Command only — enforced in main.py)
    # ------------------------------------------------------------------

    async def add_zone(self, name: str, polygon: List[geo.Coord], kind: ZoneKind = ZoneKind.RESTRICTED) -> RestrictedZone:
        zone = RestrictedZone(id=new_id("zone"), name=name, kind=kind, polygon=polygon)
        self.zones[zone.id] = zone
        await self._reconcile_routes_against_zones(newly_added=zone)
        await self.manager.broadcast({"type": "zone_created", "zone": zone.model_dump()})
        return zone

    async def update_zone(self, zone_id: str, polygon: List[geo.Coord]) -> RestrictedZone:
        zone = self.zones.get(zone_id)
        if zone is None:
            raise ValueError("Zone not found")
        if len(polygon) < 3:
            raise ValueError("A zone polygon needs at least 3 points")
        zone.polygon = polygon
        await self._reconcile_routes_against_zones()
        await self.manager.broadcast({"type": "zone_updated", "zone": zone.model_dump()})
        return zone

    async def delete_zone(self, zone_id: str):
        self.zones.pop(zone_id, None)
        await self._reconcile_routes_against_zones()
        await self.manager.broadcast({"type": "zone_deleted", "zone_id": zone_id})

    async def _reconcile_routes_against_zones(self, newly_added: Optional[RestrictedZone] = None):
        """Invariant enforced after every zone mutation: no active ship
        route may intersect any active zone. Also handles a zone being
        drawn directly on top of a ship (immediate breach)."""
        zone_polys = {z.id: Polygon(z.polygon) for z in self.zones.values()}

        for ship in self.ships.values():
            if ship.status in (ShipStatus.ARRIVED,):
                continue

            if newly_added is not None:
                newly_poly = zone_polys.get(newly_added.id)
                if newly_poly is not None and newly_poly.contains(Point(ship.position)):
                    self._fire_alert(
                        AlertType.GEOFENCE_BREACH, AlertSeverity.CRITICAL, [ship.id],
                        f"{ship.name} is inside newly-declared zone '{newly_added.name}'. Rerouting out.",
                        {"zone_id": newly_added.id},
                    )
                    self._compute_route(ship, reason_status=ShipStatus.REROUTING)
                    continue

            if ship.status == ShipStatus.STRANDED:
                # route is empty by construction (see _compute_route) — always
                # worth retrying, e.g. a zone that boxed it in may have been
                # edited or deleted since.
                self._compute_route(ship, reason_status=ShipStatus.REROUTING)
                continue

            remaining_route = ship.route[ship.route_index:]
            union_zones = _union_or_none(zone_polys.values())
            if remaining_route and geo.path_intersects_zone(remaining_route, union_zones):
                self._compute_route(ship, reason_status=ShipStatus.REROUTING)

    # ------------------------------------------------------------------
    # Directives / captain responses
    # ------------------------------------------------------------------

    async def issue_directive(self, ship_id: str, action: DirectiveAction,
                               target_port: Optional[str] = None,
                               waypoint: Optional[geo.Coord] = None) -> Directive:
        directive = Directive(ship_id=ship_id, action=action, target_port=target_port, waypoint=waypoint)
        self.directives[directive.id] = directive
        ship = self.ships[ship_id]
        ship.last_directive = directive.id
        await self.manager.broadcast({"type": "directive_issued", "directive": directive.model_dump()})
        return directive

    async def apply_captain_response(self, resp: CaptainResponse) -> dict:
        directive = self.directives.get(resp.directive_id)
        ship = self.ships.get(resp.ship_id)
        if directive is None or ship is None:
            raise ValueError("unknown directive or ship")

        if resp.response == CaptainResponseType.ACCEPT:
            directive.status = "accepted"
            if directive.action == DirectiveAction.REROUTE_TO_PORT and directive.target_port:
                self._compute_route(ship, ShipStatus.REROUTING, target_port=directive.target_port)
            elif directive.action == DirectiveAction.DIVERT_TO_WAYPOINT and directive.waypoint:
                self._compute_route(ship, ShipStatus.REROUTING, target_waypoint=tuple(directive.waypoint))
            elif directive.action == DirectiveAction.HOLD_POSITION:
                ship.status = ShipStatus.STOPPED
                ship.route = [ship.position]
                ship.route_index = 0
            await self.manager.broadcast({"type": "directive_resolved", "directive": directive.model_dump(),
                                           "ship": ship.model_dump()})
        else:
            directive.status = "escalated"
            await self.create_distress(resp.ship_id, resp.distress_message or "Captain escalated without details.")
            await self.manager.broadcast({"type": "directive_resolved", "directive": directive.model_dump()})
        return {"directive": directive.model_dump(), "ship": ship.model_dump()}

    # ------------------------------------------------------------------
    # Distress / AI
    # ------------------------------------------------------------------

    async def create_distress(self, ship_id: str, raw_message: str) -> DistressReport:
        ship = self.ships[ship_id]
        parsed = await ai.parse_distress(raw_message)
        report = DistressReport(
            ship_id=ship_id, raw_message=raw_message,
            severity=AlertSeverity(parsed["severity"]), issue=parsed["issue"],
            injuries=int(parsed.get("injuries", 0)), damage_estimate_usd=int(parsed.get("damage_estimate_usd", 0)),
            ai_parsed=parsed.get("ai_parsed", False),
        )
        await self.storage.save_distress(report.model_dump())
        ship.status = ShipStatus.DISTRESSED

        alert = self._fire_alert(
            AlertType.DISTRESS, report.severity, [ship_id],
            f"{ship.name}: {parsed.get('summary', parsed['issue'])}",
            {"injuries": report.injuries, "damage_estimate_usd": report.damage_estimate_usd,
             "raw_message": raw_message, "ai_parsed": report.ai_parsed},
        )
        # Extra: auto-generate an incident report for the log (beyond the brief).
        self._spawn(self._attach_incident_report(alert.id, ship, report))
        await self.manager.broadcast({"type": "distress_report", "report": report.model_dump(),
                                       "ship": ship.model_dump()})
        return report

    async def _attach_incident_report(self, alert_id: str, ship: Ship, report: DistressReport):
        incident = {
            "ship_name": ship.name, "type": "distress", "message": report.issue,
            "severity": report.severity.value, "injuries": report.injuries,
            "damage_estimate_usd": report.damage_estimate_usd, "created_at": report.created_at,
        }
        text = await ai.generate_incident_report(incident)
        if alert_id in self.alerts:
            await self.storage.update_alert(alert_id, report=text)
            await self.manager.broadcast({"type": "incident_report", "alert_id": alert_id, "report": text})

    # ------------------------------------------------------------------
    # Ship-to-ship assistance (PDF bonus)
    # ------------------------------------------------------------------

    async def request_assistance(self, ship_id: str, kind: AssistanceRequestType) -> AssistanceRequest:
        req = AssistanceRequest(requesting_ship_id=ship_id, kind=kind)
        self.assistance_requests[req.id] = req
        ship = self.ships[ship_id]
        nearby = [
            other.id for other in self.ships.values()
            if other.id != ship_id and other.status not in (ShipStatus.DISTRESSED, ShipStatus.STOPPED, ShipStatus.STRANDED)
            and geo.haversine_km(ship.position, other.position) <= 60
        ]
        self._fire_alert(
            AlertType.ASSISTANCE_REQUEST, AlertSeverity.WARNING, [ship_id] + nearby,
            f"{ship.name} is requesting {kind.value.replace('_',' ')} assistance.",
            {"request_id": req.id, "nearby_ship_ids": nearby},
        )
        await self.manager.broadcast({"type": "assistance_request", "request": req.model_dump(),
                                       "nearby_ship_ids": nearby})
        return req

    async def respond_assistance(self, request_id: str, responding_ship_id: str, accept: bool):
        req = self.assistance_requests.get(request_id)
        if req is None:
            raise ValueError("unknown assistance request")
        req.status = "accepted" if accept else "declined"
        req.responding_ship_id = responding_ship_id if accept else None
        await self.manager.broadcast({"type": "assistance_response", "request": req.model_dump()})
        return req

    # ------------------------------------------------------------------
    # Multi-route candidates (PDF bonus)
    # ------------------------------------------------------------------

    def route_options(self, ship_id: str) -> List[dict]:
        ship = self.ships[ship_id]
        goal = geo.port_approach_point(ship.destination_port)
        zones = self._zone_polygons(exclude_containing=ship.position)
        options = []
        for bias, label in (("fast", "Fastest"), ("safe", "Weather-safe"), ("efficient", "Fuel-efficient")):
            r = geo.astar_route(tuple(ship.position), goal, restricted_zones=zones,
                                 weather_penalty_fn=weather_service.routing_penalty, route_bias=bias)
            if r.found:
                est_fuel = r.distance_km * ship.fuel_burn_tons_per_km
                options.append({
                    "bias": bias, "label": label, "waypoints": r.waypoints,
                    "distance_km": round(r.distance_km, 1),
                    "estimated_fuel_tons": round(est_fuel, 1),
                    "estimated_fuel_percent": round(
                        100.0 * est_fuel / max(ship.initial_fuel_tons, 1.0), 1
                    ),
                })
        return options

    async def select_route(self, ship_id: str, waypoints: List[geo.Coord]):
        ship = self.ships[ship_id]
        ship.route = [tuple(p) for p in waypoints]
        ship.route_index = 0
        ship.distance_remaining_km = sum(
            geo.haversine_km(ship.route[i], ship.route[i + 1]) for i in range(len(ship.route) - 1)
        )
        ship.status = ShipStatus.REROUTING
        self._check_fuel_projection(ship)
        await self.manager.broadcast({"type": "ship_update", "ship": ship.model_dump()})

    # ------------------------------------------------------------------
    # AI fleet advisor (PDF bonus, on-demand to respect free-tier limits)
    # ------------------------------------------------------------------

    async def advisor_suggestions(self) -> dict:
        fleet_summary = {"ships": [
            {
                "id": s.id, "name": s.name,
                "fuel_tons": round(s.fuel_tons, 1),
                "fuel_percent": round(s.fuel_percent, 1),
                "status": s.status.value,
            }
            for s in self.ships.values()
        ]}
        alerts_summary = [a.model_dump() for a in list(self.alerts.values())[-30:]]
        result = await ai.fleet_advisor_suggestions(fleet_summary, alerts_summary)
        await self.manager.broadcast({"type": "advisor_suggestions", **result})
        return result

    # ------------------------------------------------------------------
    # Tick loop
    # ------------------------------------------------------------------

    async def _run_forever(self):
        while True:
            started = time.monotonic()
            try:
                await self._tick()
            except Exception:  # noqa: BLE001 — one bad tick must not kill the fleet
                log.exception("tick failed")
            elapsed = time.monotonic() - started
            await asyncio.sleep(max(0.0, (1.0 / TICK_HZ) - elapsed))

    async def _tick(self):
        self._tick_count += 1
        dt_seconds = 1.0 / TICK_HZ

        for ship in self.ships.values():
            self._advance_ship(ship, dt_seconds)

        self._check_geofence_breaches()
        self._check_proximity_and_cpa()
        self._check_predictive_zone_entry()

        state = [s.model_dump() for s in self.ships.values()]
        await self.manager.broadcast({
            "type": "fleet_state", "tick": self._tick_count, "server_time_ms": now_ms(),
            "ships": state,
        })
        await self.storage.maybe_snapshot(state)

    def _advance_ship(self, ship: Ship, dt_seconds: float):
        if ship.status in (ShipStatus.ARRIVED, ShipStatus.STRANDED, ShipStatus.STOPPED) or not ship.route:
            return

        ship.in_adverse_weather = weather_service.is_adverse_at(ship.position)
        speed_kmh = ship.speed_knots * 1.852
        distance_step_km = speed_kmh * (dt_seconds / 3600.0)

        remaining_step = distance_step_km

        while remaining_step > 0 and ship.route_index < len(ship.route) - 1:
            target = ship.route[ship.route_index + 1]
            bearing = geo.bearing_deg(ship.position, target)
            ship.heading = bearing
            leg_dist = geo.haversine_km(ship.position, target)
            if leg_dist <= remaining_step:
                ship.position = target
                remaining_step -= leg_dist
                ship.route_index += 1
            else:
                ship.position = geo.destination_point(ship.position, bearing, remaining_step)
                remaining_step = 0

        if ship.route_index >= len(ship.route) - 1 and ship.status not in (ShipStatus.DISTRESSED,):
            ship.status = ShipStatus.ARRIVED
            ship.speed_knots = 0

        ship.distance_remaining_km = sum(
            geo.haversine_km(ship.route[i], ship.route[i + 1])
            for i in range(ship.route_index, len(ship.route) - 1)
        )

        weather_multiplier = 1.3 if ship.in_adverse_weather else 1.0
        burn_tons = distance_step_km * ship.fuel_burn_tons_per_km * weather_multiplier
        ship.fuel_tons = max(0.0, ship.fuel_tons - burn_tons)
        ship.fuel_percent = (
            100.0 * ship.fuel_tons / max(ship.initial_fuel_tons, 1.0)
        )
        if ship.fuel_tons > 0 and ship.status not in (ShipStatus.ARRIVED, ShipStatus.DISTRESSED, ShipStatus.STOPPED, ShipStatus.STRANDED):
            self._check_fuel_projection(ship)
        if ship.fuel_tons <= 0 and ship.status not in (ShipStatus.ARRIVED,):
            ship.status = ShipStatus.STOPPED
            ship.speed_knots = 0
            self._fire_alert(
                AlertType.INSUFFICIENT_FUEL, AlertSeverity.CRITICAL, [ship.id],
                f"{ship.name} has run out of fuel and is dead in the water.", {},
            )
        ship.updated_at = now_ms()

    def _check_geofence_breaches(self):
        for ship in self.ships.values():
            if ship.status == ShipStatus.ARRIVED:
                continue
            pos = Point(ship.position)
            for zone in self.zones.values():
                key = (ship.id, zone.id)
                inside = Polygon(zone.polygon).contains(pos)
                if inside and key not in self._fired_breach_for:
                    # a ship that drifts into a zone mid-voyage (not the
                    # add_zone-triggered case, which is handled separately)
                    self._fired_breach_for.add(key)
                    self._fire_alert(
                        AlertType.GEOFENCE_BREACH, AlertSeverity.CRITICAL, [ship.id],
                        f"{ship.name} has entered restricted zone '{zone.name}'.", {"zone_id": zone.id},
                    )
                    if ship.status != ShipStatus.DISTRESSED:
                        self._compute_route(ship, ShipStatus.REROUTING)
                elif not inside:
                    self._fired_breach_for.discard(key)

    def _check_proximity_and_cpa(self):
        active_ships = [s for s in self.ships.values() if s.status != ShipStatus.ARRIVED]
        current_proximity = set()
        current_cpa = set()

        for a, b in itertools.combinations(active_ships, 2):
            pair_key = tuple(sorted((a.id, b.id)))
            dist = geo.haversine_km(a.position, b.position)

            if dist <= PROXIMITY_THRESHOLD_KM:
                current_proximity.add(pair_key)
                if pair_key not in self._active_proximity_pairs:
                    self._fire_alert(
                        AlertType.PROXIMITY, AlertSeverity.CRITICAL, [a.id, b.id],
                        f"{a.name} and {b.name} are within {dist:.2f} km of each other.",
                        {"distance_km": round(dist, 3)},
                    )
                continue  # already critical-close; no need for a separate CPA warning

            t_min, min_dist = geo.cpa(
                a.position, a.heading, a.speed_knots, b.position, b.heading, b.speed_knots,
                horizon_minutes=CPA_HORIZON_MINUTES,
            )
            if min_dist <= CPA_WARNING_KM and t_min > 0.1:
                current_cpa.add(pair_key)
                if pair_key not in self._active_cpa_pairs:
                    self._fire_alert(
                        AlertType.PREDICTED_COLLISION, AlertSeverity.WARNING, [a.id, b.id],
                        f"{a.name} and {b.name} are on a course that closes to "
                        f"{min_dist:.2f} km in ~{t_min:.1f} min at current heading/speed.",
                        {"time_to_cpa_minutes": round(t_min, 1), "predicted_min_distance_km": round(min_dist, 3)},
                    )

        self._active_proximity_pairs = current_proximity
        self._active_cpa_pairs = current_cpa

    def _check_predictive_zone_entry(self):
        """Extra/bonus: warn a few minutes before a ship's current course
        would carry it into a zone, not just after the fact."""
        for ship in self.ships.values():
            if ship.status in (ShipStatus.ARRIVED, ShipStatus.STOPPED, ShipStatus.STRANDED):
                continue
            speed_kmh = ship.speed_knots * 1.852
            if speed_kmh <= 0:
                continue
            look_ahead_km = speed_kmh * (ZONE_ETA_WARNING_MINUTES / 60.0)
            future_point = geo.destination_point(ship.position, ship.heading, look_ahead_km)
            path = [ship.position, future_point]
            for zone in self.zones.values():
                key = (ship.id, zone.id)
                poly = Polygon(zone.polygon)
                already_inside = poly.contains(Point(ship.position))
                will_cross = geo.path_intersects_zone(path, poly)
                if will_cross and not already_inside and key not in self._fired_zone_eta_for:
                    self._fired_zone_eta_for.add(key)
                    self._fire_alert(
                        AlertType.PREDICTED_ZONE_ENTRY, AlertSeverity.WARNING, [ship.id],
                        f"{ship.name} will enter zone '{zone.name}' in roughly {ZONE_ETA_WARNING_MINUTES:.0f} "
                        f"minutes at its current heading if it doesn't change course.",
                        {"zone_id": zone.id, "eta_minutes": ZONE_ETA_WARNING_MINUTES},
                    )
                elif not will_cross:
                    self._fired_zone_eta_for.discard(key)


def _union_or_none(polys):
    polys = list(polys)
    if not polys:
        return Polygon()
    if len(polys) == 1:
        return polys[0]
    from shapely.ops import unary_union
    return unary_union(polys)
