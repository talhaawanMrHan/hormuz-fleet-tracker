"""ws.py — tracks every connected WebSocket client and broadcasts state to
all of them. Every connected client sees the same fleet state (Command and
every Captain alike); role only gates which REST actions a client is
allowed to perform, enforced in main.py, not what gets broadcast."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Dict, Optional

from fastapi import WebSocket

log = logging.getLogger("ws")


@dataclass
class ClientInfo:
    role: str  # "command" | "captain"
    ship_id: Optional[str] = None


class ConnectionManager:
    def __init__(self):
        self._clients: Dict[WebSocket, ClientInfo] = {}
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket, role: str, ship_id: Optional[str] = None):
        await ws.accept()
        async with self._lock:
            self._clients[ws] = ClientInfo(role=role, ship_id=ship_id)
        log.info("client connected: role=%s ship_id=%s total=%d", role, ship_id, len(self._clients))

    async def disconnect(self, ws: WebSocket):
        async with self._lock:
            self._clients.pop(ws, None)

    def count(self) -> int:
        return len(self._clients)

    async def broadcast(self, message: dict):
        payload = json.dumps(message, default=str)
        dead = []
        for ws in list(self._clients.keys()):
            try:
                await ws.send_text(payload)
            except Exception:  # noqa: BLE001 — a dropped client shouldn't break the broadcast
                dead.append(ws)
        for ws in dead:
            await self.disconnect(ws)

    async def send_to(self, ws: WebSocket, message: dict):
        try:
            await ws.send_text(json.dumps(message, default=str))
        except Exception:  # noqa: BLE001
            await self.disconnect(ws)


manager = ConnectionManager()
