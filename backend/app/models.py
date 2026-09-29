"""models.py — the shared vocabulary of the system. Every WebSocket message
and REST payload is built from these."""

from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import List, Optional, Tuple

from pydantic import BaseModel, Field

Coord = Tuple[float, float]  # (lng, lat)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def now_ms() -> int:
    return int(time.time() * 1000)


class ShipStatus(str, Enum):
    NORMAL = "normal"
    REROUTING = "rerouting"
    DISTRESSED = "distressed"
    STOPPED = "stopped"
    STRANDED = "stranded"
    INSUFFICIENT_FUEL = "insufficient_fuel"
    ARRIVED = "arrived"


class CargoType(str, Enum):
    CONTAINER = "container"
    CRUDE_OIL = "crude_oil"
    LNG = "lng"
    BULK_GRAIN = "bulk_grain"
    VEHICLES = "vehicles"
    GENERAL = "general"


class Ship(BaseModel):
    id: str
    name: str
    imo: str  # cosmetic IMO-style hull number
    position: Coord
    heading: float = 0.0          # degrees, 0 = north, clockwise
    speed_knots: float = 12.0
    max_speed_knots: float = 18.0
    destination_port: str
    # Compatibility/derived percentage plus the scenario's canonical fuel amount.
    fuel_percent: float = 100.0
    fuel_tons: float = 0.0
    initial_fuel_tons: float = 0.0
    fuel_burn_per_km: float = 0.04     # derived percent burn, kept for compatibility
    fuel_burn_tons_per_km: float = 0.1 # documented simulator assumption for the source fleet
    cargo: CargoType = CargoType.CONTAINER
    cargo_detail: str = ""
    status: ShipStatus = ShipStatus.NORMAL
    fuel_warning: bool = False   # "insufficient fuel to finish route" — independent of status
    route: List[Coord] = Field(default_factory=list)
    route_index: int = 0
    distance_remaining_km: float = 0.0
    in_adverse_weather: bool = False
    last_directive: Optional[str] = None
    updated_at: int = Field(default_factory=now_ms)


class ZoneKind(str, Enum):
    RESTRICTED = "restricted"
    STORM = "storm"


class RestrictedZone(BaseModel):
    id: str
    name: str
    kind: ZoneKind = ZoneKind.RESTRICTED
    polygon: List[Coord]
    created_by: str = "command"
    created_at: int = Field(default_factory=now_ms)


class AlertSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertType(str, Enum):
    GEOFENCE_BREACH = "geofence_breach"
    PROXIMITY = "proximity"
    PREDICTED_COLLISION = "predicted_collision"
    PREDICTED_ZONE_ENTRY = "predicted_zone_entry"
    PREDICTED_FUEL_SHORTAGE = "predicted_fuel_shortage"
    STRANDED = "stranded"
    INSUFFICIENT_FUEL = "insufficient_fuel"
    DISTRESS = "distress"
    ASSISTANCE_REQUEST = "assistance_request"
    ADVISOR_SUGGESTION = "advisor_suggestion"


class Alert(BaseModel):
    id: str = Field(default_factory=lambda: new_id("alert"))
    type: AlertType
    severity: AlertSeverity
    ship_ids: List[str] = Field(default_factory=list)
    message: str
    detail: dict = Field(default_factory=dict)
    created_at: int = Field(default_factory=now_ms)
    acknowledged: bool = False
    resolved: bool = False


class DirectiveAction(str, Enum):
    REROUTE_TO_PORT = "reroute_to_port"
    DIVERT_TO_WAYPOINT = "divert_to_waypoint"
    HOLD_POSITION = "hold_position"


class Directive(BaseModel):
    id: str = Field(default_factory=lambda: new_id("dir"))
    ship_id: str
    action: DirectiveAction
    target_port: Optional[str] = None
    waypoint: Optional[Coord] = None
    issued_by: str = "command"
    created_at: int = Field(default_factory=now_ms)
    status: str = "pending"  # pending | accepted | escalated


class CaptainResponseType(str, Enum):
    ACCEPT = "accept"
    ESCALATE_DISTRESS = "escalate_distress"


class CaptainResponse(BaseModel):
    directive_id: str
    ship_id: str
    response: CaptainResponseType
    distress_message: Optional[str] = None


class DistressReport(BaseModel):
    id: str = Field(default_factory=lambda: new_id("distress"))
    ship_id: str
    raw_message: str
    severity: AlertSeverity = AlertSeverity.WARNING
    issue: str = ""
    injuries: int = 0
    damage_estimate_usd: int = 0
    ai_parsed: bool = False
    created_at: int = Field(default_factory=now_ms)


class AssistanceRequestType(str, Enum):
    FUEL_TRANSFER = "fuel_transfer"
    MEDICAL_AID = "medical_aid"
    ESCORT = "escort"
    CARGO_OFFLOAD = "cargo_offload"


class AssistanceRequest(BaseModel):
    id: str = Field(default_factory=lambda: new_id("assist"))
    requesting_ship_id: str
    responding_ship_id: Optional[str] = None
    kind: AssistanceRequestType
    status: str = "open"  # open | accepted | declined
    created_at: int = Field(default_factory=now_ms)
