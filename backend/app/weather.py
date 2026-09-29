"""
weather.py — real weather from Open-Meteo (no API key needed, generous free
tier: https://open-meteo.com). Two endpoints are combined:
  - marine-api.open-meteo.com  -> wave height
  - api.open-meteo.com         -> wind speed

Ships need weather *now*, but A* route costing needs a cheap synchronous
lookup it can call hundreds of times per route. So a background task
samples a coarse grid over the operational area every few minutes and
caches it; both the simulator and the router read from that cache instead
of making a live HTTP call per lookup.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Dict, Tuple

import httpx

from . import geo

log = logging.getLogger("weather")

MARINE_URL = "https://marine-api.open-meteo.com/v1/marine"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# Thresholds for what counts as "adverse" for a cargo ship.
ADVERSE_WAVE_M = 1.8
ADVERSE_WIND_KMH = 35.0

SAMPLE_COLS = 5
SAMPLE_ROWS = 4
REFRESH_SECONDS = 600  # real weather doesn't change fast enough to poll every tick


@dataclass
class WeatherConditions:
    wave_height_m: float
    wind_speed_kmh: float
    is_adverse: bool
    fetched_at: float


class WeatherService:
    def __init__(self):
        self._cache: Dict[Tuple[int, int], WeatherConditions] = {}
        self._client = httpx.AsyncClient(timeout=8.0)
        self._task: asyncio.Task | None = None
        self._sample_points = self._build_sample_grid()

    def _build_sample_grid(self):
        pts = []
        for c in range(SAMPLE_COLS):
            for r in range(SAMPLE_ROWS):
                lng = geo.BBOX["min_lng"] + (c + 0.5) * (geo.BBOX["max_lng"] - geo.BBOX["min_lng"]) / SAMPLE_COLS
                lat = geo.BBOX["min_lat"] + (r + 0.5) * (geo.BBOX["max_lat"] - geo.BBOX["min_lat"]) / SAMPLE_ROWS
                pts.append((c, r, lng, lat))
        return pts

    def _bucket(self, coord: geo.Coord) -> Tuple[int, int]:
        lng, lat = coord
        c = int((lng - geo.BBOX["min_lng"]) / (geo.BBOX["max_lng"] - geo.BBOX["min_lng"]) * SAMPLE_COLS)
        r = int((lat - geo.BBOX["min_lat"]) / (geo.BBOX["max_lat"] - geo.BBOX["min_lat"]) * SAMPLE_ROWS)
        c = max(0, min(SAMPLE_COLS - 1, c))
        r = max(0, min(SAMPLE_ROWS - 1, r))
        return (c, r)

    async def _fetch_point(self, lng: float, lat: float) -> WeatherConditions:
        wave_m = 0.0
        wind_kmh = 0.0
        try:
            resp = await self._client.get(
                MARINE_URL,
                params={"latitude": lat, "longitude": lng, "current": "wave_height"},
            )
            if resp.status_code == 200:
                wave_m = float(resp.json().get("current", {}).get("wave_height") or 0.0)
        except Exception as exc:  # noqa: BLE001 — weather must never crash the sim
            log.warning("marine fetch failed (%.2f,%.2f): %s", lat, lng, exc)

        try:
            resp = await self._client.get(
                FORECAST_URL,
                params={"latitude": lat, "longitude": lng, "current": "wind_speed_10m"},
            )
            if resp.status_code == 200:
                wind_kmh = float(resp.json().get("current", {}).get("wind_speed_10m") or 0.0)
        except Exception as exc:  # noqa: BLE001
            log.warning("forecast fetch failed (%.2f,%.2f): %s", lat, lng, exc)

        adverse = wave_m >= ADVERSE_WAVE_M or wind_kmh >= ADVERSE_WIND_KMH
        return WeatherConditions(wave_m, wind_kmh, adverse, time.time())

    async def _refresh_all(self):
        for c, r, lng, lat in self._sample_points:
            conditions = await self._fetch_point(lng, lat)
            self._cache[(c, r)] = conditions
            await asyncio.sleep(0.2)  # be polite to the free API

    async def _loop(self):
        while True:
            try:
                await self._refresh_all()
            except Exception as exc:  # noqa: BLE001
                log.warning("weather refresh loop error: %s", exc)
            await asyncio.sleep(REFRESH_SECONDS)

    def start(self):
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def stop(self):
        if self._task:
            self._task.cancel()
        await self._client.aclose()

    def conditions_at(self, coord: geo.Coord) -> WeatherConditions:
        bucket = self._bucket(coord)
        cached = self._cache.get(bucket)
        if cached is None:
            return WeatherConditions(0.0, 0.0, False, 0.0)
        return cached

    def is_adverse_at(self, coord: geo.Coord) -> bool:
        return self.conditions_at(coord).is_adverse

    def routing_penalty(self, coord: geo.Coord) -> float:
        """0..~2 extra cost multiplier for the A* router — worse weather = more cost."""
        c = self.conditions_at(coord)
        penalty = 0.0
        if c.wave_height_m > 0:
            penalty += min(1.5, c.wave_height_m / ADVERSE_WAVE_M) 
        if c.wind_speed_kmh > 0:
            penalty += min(1.0, c.wind_speed_kmh / ADVERSE_WIND_KMH) * 0.5
        return penalty


weather_service = WeatherService()
