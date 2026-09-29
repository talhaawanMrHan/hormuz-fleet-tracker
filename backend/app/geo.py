"""
geo.py
Everything geospatial: the operational area, the navigable-water polygon,
ports, a grid-based A* router, and the maths for distance / closest-point-
of-approach (CPA) collision prediction.

NOTE ON THE POLYGON: real coastline data for the Strait of Hormuz was not
provided with this build (the hackathon brief says organisers hand teams a
fleet.json with the polygon baked in). This is a hand-built, deliberately
simplified stand-in that keeps the correct shape of the problem: a wide
Persian Gulf basin, a narrow chokepoint around the Musandam peninsula /
Larak-Qeshm area, opening into the Gulf of Oman. It is documented here as
an assumption, per the brief's own ground rules.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from shapely.geometry import LineString, Point, Polygon
from shapely.ops import nearest_points, polygonize, unary_union

Coord = Tuple[float, float]  # (lng, lat)

# ---------------------------------------------------------------------------
# Operational area
# ---------------------------------------------------------------------------

# Canonical scenario geometry from fleet.json. The supplied JSON uses [lat, lng];
# internal geometry uses (lng, lat) tuples because Shapely/Leaflet code is built that way.
BBOX = {"min_lng": 47.5, "max_lng": 60.0, "min_lat": 22.0, "max_lat": 30.5}

# Convert the scenario's [lat, lng] polygon to internal (lng, lat).
_NAVIGABLE_SOURCE = [
    [29.80,48.60],[29.50,50.00],[28.80,50.80],[27.80,52.00],[26.70,53.50],
    [26.30,55.00],[26.65,56.10],[26.50,56.40],[26.00,56.80],[25.50,57.50],
    [25.50,58.50],[25.00,60.00],[22.00,60.00],[22.50,60.00],[23.80,58.80],
    [24.50,57.20],[25.20,56.50],[26.45,56.45],[26.30,55.90],[26.00,55.50],
    [25.30,54.50],[24.80,53.00],[25.30,52.00],[26.40,51.50],[26.50,50.30],
    [27.50,49.80],[28.50,49.00],[29.50,48.30],[29.80,48.60]
]
NAVIGABLE_WATER = [(lng, lat) for lat, lng in _NAVIGABLE_SOURCE]
# Shapely reports a self-intersection in the organizer-provided ring at the
# Strait crossing. Keep every source vertex byte-for-byte in NAVIGABLE_WATER,
# but polygonize the ring and add a narrow bridge across the two main pieces so
# the supplied scenario remains one navigable water area. This is a runtime
# geometry repair only; the canonical JSON is never modified.
_raw_line = LineString(NAVIGABLE_WATER)
_raw_parts = sorted(list(polygonize(unary_union(_raw_line))), key=lambda p: p.area, reverse=True)
if len(_raw_parts) >= 2:
    _a, _b = nearest_points(_raw_parts[0], _raw_parts[1])
    _strait_bridge = LineString([_a, _b]).buffer(0.04)
    NAVIGABLE_POLYGON = unary_union(_raw_parts + [_strait_bridge]).buffer(0)
else:
    NAVIGABLE_POLYGON = Polygon(NAVIGABLE_WATER).buffer(0)

# Canonical port IDs/names/coordinates from fleet.json; coordinates are internal (lng, lat).
_PORT_SOURCE = [
    ("KWT-1", "Kuwait City", [29.48,48.34]),
    ("BUS-1", "Bushehr", [28.83,50.73]),
    ("DMM-1", "Dammam", [26.56,50.30]),
    ("BAH-1", "Manama", [26.50,50.55]),
    ("DOH-1", "Doha", [25.46,51.95]),
    ("AUH-1", "Abu Dhabi", [25.22,54.18]),
    ("DXB-1", "Jebel Ali", [25.50,54.75]),
    ("BND-1", "Bandar Abbas", [26.62,56.11]),
    ("SOH-1", "Sohar", [24.72,57.02]),
    ("MCT-1", "Muscat", [23.92,58.58]),
]
PORTS = {pid: (pos[1], pos[0]) for pid, _name, pos in _PORT_SOURCE}
PORT_NAMES = {pid: name for pid, name, _pos in _PORT_SOURCE}
PORT_SOURCE_POSITIONS = {pid: tuple(pos) for pid, _name, pos in _PORT_SOURCE}  # [lat, lng]


def _snap_into_navigable(pt: Coord, inset: float = 0.02) -> Coord:
    """Move an out-of-water port to the nearest point inside navigable water."""
    point = Point(pt)
    if NAVIGABLE_POLYGON.contains(point):
        return pt
    nearest = nearest_points(NAVIGABLE_POLYGON.boundary, point)[0]
    # Move a tiny amount toward the polygon centroid so the route starts on water.
    centroid = NAVIGABLE_POLYGON.centroid
    dx = centroid.x - nearest.x
    dy = centroid.y - nearest.y
    mag = math.hypot(dx, dy) or 1.0
    return (nearest.x + inset * dx / mag, nearest.y + inset * dy / mag)


def port_approach_point(port_id: str) -> Coord:
    if port_id not in PORTS:
        raise KeyError(f"Unknown port id: {port_id}")
    return _snap_into_navigable(PORTS[port_id])


EARTH_RADIUS_KM = 6371.0088


def haversine_km(a: Coord, b: Coord) -> float:
    lon1, lat1 = a
    lon2, lat2 = b
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(h)))


def bearing_deg(a: Coord, b: Coord) -> float:
    lon1, lat1 = math.radians(a[0]), math.radians(a[1])
    lon2, lat2 = math.radians(b[0]), math.radians(b[1])
    dlon = lon2 - lon1
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def destination_point(origin: Coord, heading_deg: float, distance_km: float) -> Coord:
    """Move `distance_km` from origin along heading_deg (great-circle)."""
    lon1, lat1 = math.radians(origin[0]), math.radians(origin[1])
    brng = math.radians(heading_deg)
    d_r = distance_km / EARTH_RADIUS_KM
    lat2 = math.asin(math.sin(lat1) * math.cos(d_r) + math.cos(lat1) * math.sin(d_r) * math.cos(brng))
    lon2 = lon1 + math.atan2(
        math.sin(brng) * math.sin(d_r) * math.cos(lat1),
        math.cos(d_r) - math.sin(lat1) * math.sin(lat2),
    )
    return (math.degrees(lon2), math.degrees(lat2))


# ---------------------------------------------------------------------------
# Grid-based A*
# ---------------------------------------------------------------------------

# The source bbox is larger than the original generated fleet, so use a finer
# grid to resolve the narrow Strait crossing without blocking valid routes.
GRID_COLS = 180
GRID_ROWS = 120


@dataclass
class RouteResult:
    waypoints: List[Coord]
    distance_km: float
    found: bool


def _cell_center(col: int, row: int) -> Coord:
    lng = BBOX["min_lng"] + (col + 0.5) * (BBOX["max_lng"] - BBOX["min_lng"]) / GRID_COLS
    lat = BBOX["min_lat"] + (row + 0.5) * (BBOX["max_lat"] - BBOX["min_lat"]) / GRID_ROWS
    return (lng, lat)


def _latlng_to_cell(pt: Coord) -> Tuple[int, int]:
    col = int((pt[0] - BBOX["min_lng"]) / (BBOX["max_lng"] - BBOX["min_lng"]) * GRID_COLS)
    row = int((pt[1] - BBOX["min_lat"]) / (BBOX["max_lat"] - BBOX["min_lat"]) * GRID_ROWS)
    return max(0, min(GRID_COLS - 1, col)), max(0, min(GRID_ROWS - 1, row))


NEIGHBORS = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]


def _blocked(col: int, row: int, obstacles: Polygon | None) -> bool:
    center = Point(_cell_center(col, row))
    if not NAVIGABLE_POLYGON.contains(center):
        return True
    if obstacles is not None and obstacles.contains(center):
        return True
    return False


def astar_route(
    start: Coord,
    goal: Coord,
    restricted_zones: Sequence[Polygon] = (),
    weather_penalty_fn=None,
    route_bias: str = "balanced",
) -> RouteResult:
    """
    A* over a coarse lat/lng grid. `route_bias` lets the caller generate the
    multiple-candidate-routes bonus cheaply by reweighting the same search:
      - "fast":      distance only, ignores weather cost
      - "safe":      heavy weather penalty, prefers calmer water
      - "efficient": distance + moderate weather penalty (default "balanced")
    """
    obstacles = unary_union(restricted_zones) if restricted_zones else None

    start = _snap_into_navigable(start)
    goal = _snap_into_navigable(goal)
    start_cell = _latlng_to_cell(start)
    goal_cell = _latlng_to_cell(goal)

    # A snapped point can still land in a blocked cell on a coarse grid;
    # widen the snap inset a few times before giving up.
    inset = 0.02
    while _blocked(*goal_cell, obstacles) and inset < 0.3:
        inset += 0.04
        goal = _snap_into_navigable(goal, inset=inset)
        goal_cell = _latlng_to_cell(goal)
    inset = 0.02
    while _blocked(*start_cell, None) and inset < 0.3:
        inset += 0.04
        start = _snap_into_navigable(start, inset=inset)
        start_cell = _latlng_to_cell(start)

    if _blocked(*goal_cell, obstacles):
        return RouteResult([], 0.0, False)

    def cell_cost(col: int, row: int) -> float:
        base = 1.0
        if weather_penalty_fn is not None and route_bias != "fast":
            multiplier = 3.0 if route_bias == "safe" else 1.0
            base += weather_penalty_fn(_cell_center(col, row)) * multiplier
        return base

    open_heap: List[Tuple[float, Tuple[int, int]]] = [(0.0, start_cell)]
    came_from = {}
    g_score = {start_cell: 0.0}
    visited = set()

    def h(cell):
        return haversine_km(_cell_center(*cell), _cell_center(*goal_cell))

    steps = 0
    max_steps = GRID_COLS * GRID_ROWS * 4
    while open_heap and steps < max_steps:
        steps += 1
        _, current = heapq.heappop(open_heap)
        if current in visited:
            continue
        visited.add(current)
        if current == goal_cell:
            break
        for dc, dr in NEIGHBORS:
            nxt = (current[0] + dc, current[1] + dr)
            if not (0 <= nxt[0] < GRID_COLS and 0 <= nxt[1] < GRID_ROWS):
                continue
            if nxt in visited or _blocked(*nxt, obstacles):
                continue
            step_dist = haversine_km(_cell_center(*current), _cell_center(*nxt))
            tentative = g_score[current] + step_dist * cell_cost(*nxt)
            if tentative < g_score.get(nxt, math.inf):
                g_score[nxt] = tentative
                came_from[nxt] = current
                heapq.heappush(open_heap, (tentative + h(nxt), nxt))

    if goal_cell not in came_from and goal_cell != start_cell:
        return RouteResult([], 0.0, False)

    path_cells = [goal_cell]
    cur = goal_cell
    while cur != start_cell:
        cur = came_from.get(cur)
        if cur is None:
            return RouteResult([], 0.0, False)
        path_cells.append(cur)
    path_cells.reverse()

    waypoints = [start] + [_cell_center(*c) for c in path_cells[1:-1]] + [goal]
    waypoints = _simplify(waypoints)
    dist = sum(haversine_km(waypoints[i], waypoints[i + 1]) for i in range(len(waypoints) - 1))
    return RouteResult(waypoints, dist, True)


def _simplify(pts: List[Coord], tolerance_deg: float = 0.05) -> List[Coord]:
    """Collapse near-collinear runs so the ship doesn't zig-zag on the grid."""
    if len(pts) <= 2:
        return pts
    simplified = LineString(pts).simplify(tolerance_deg, preserve_topology=False)
    return list(simplified.coords)


def path_intersects_zone(waypoints: Sequence[Coord], zone: Polygon) -> bool:
    if len(waypoints) < 2:
        return False
    return LineString(waypoints).intersects(zone)


def nearest_point_on_path(position: Coord, waypoints: Sequence[Coord]) -> int:
    """Index of the waypoint segment closest to `position` (for resuming a path)."""
    best_i, best_d = 0, math.inf
    for i in range(len(waypoints) - 1):
        seg = LineString([waypoints[i], waypoints[i + 1]])
        d = seg.distance(Point(position))
        if d < best_d:
            best_d, best_i = d, i
    return best_i


# ---------------------------------------------------------------------------
# Closest Point of Approach (CPA) — extra feature beyond the brief.
# The brief only asks for "are two ships within 2km right now". CPA looks
# ahead: given both ships' current course + speed, will they *become* a
# collision risk in the next few minutes, even if they're not close yet.
# ---------------------------------------------------------------------------

def velocity_vector_kmh(heading_deg: float, speed_knots: float) -> Tuple[float, float]:
    speed_kmh = speed_knots * 1.852
    rad = math.radians(heading_deg)
    # vx = east component, vy = north component, in km/h, using a local
    # flat-earth approximation which is fine over these short horizons.
    return speed_kmh * math.sin(rad), speed_kmh * math.cos(rad)


def cpa(
    pos_a: Coord, heading_a: float, speed_a: float,
    pos_b: Coord, heading_b: float, speed_b: float,
    horizon_minutes: float = 12.0,
) -> Tuple[float, float]:
    """
    Returns (time_to_cpa_minutes, predicted_min_distance_km), clamped to the
    horizon. Positions are converted to a local km-based plane centered on
    ship A using an equirectangular approximation (fine at this scale).
    """
    lat0 = math.radians((pos_a[1] + pos_b[1]) / 2)
    kx = 111.320 * math.cos(lat0)
    ky = 110.574

    ax, ay = 0.0, 0.0
    bx = (pos_b[0] - pos_a[0]) * kx
    by = (pos_b[1] - pos_a[1]) * ky

    vax, vay = velocity_vector_kmh(heading_a, speed_a)
    vbx, vby = velocity_vector_kmh(heading_b, speed_b)

    rvx, rvy = (vbx - vax) / 60.0, (vby - vay) / 60.0  # km per minute
    rx, ry = bx - ax, by - ay

    denom = rvx * rvx + rvy * rvy
    if denom < 1e-9:
        t_star = 0.0
    else:
        t_star = -(rx * rvx + ry * rvy) / denom
    t_star = max(0.0, min(horizon_minutes, t_star))

    close_x = rx + rvx * t_star
    close_y = ry + rvy * t_star
    min_dist = math.hypot(close_x, close_y)
    return t_star, min_dist
