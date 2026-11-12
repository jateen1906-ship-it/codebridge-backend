"""In-process WebSocket connection manager for live Notification Service event delivery."""

from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict
from typing import Any

from fastapi import WebSocket


class NotificationWebSocketManager:
    """Tracks active WebSocket connections per user and broadcasts live events."""

    def __init__(self) -> None:
        self._connections: dict[uuid.UUID, set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def connect(self, user_id: uuid.UUID, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections[user_id].add(websocket)

    async def disconnect(self, user_id: uuid.UUID, websocket: WebSocket) -> None:
        async with self._lock:
            sockets = self._connections.get(user_id)
            if sockets is not None:
                sockets.discard(websocket)
                if not sockets:
                    self._connections.pop(user_id, None)

    async def active_count(self, user_id: uuid.UUID | None = None) -> int:
        async with self._lock:
            if user_id is not None:
                return len(self._connections.get(user_id, ()))
            return sum(len(s) for s in self._connections.values())

    async def broadcast_to_user(self, user_id: uuid.UUID, payload: dict[str, Any]) -> int:
        async with self._lock:
            targets = list(self._connections.get(user_id, ()))
        if not targets:
            return 0

        delivered = 0
        dead: list[WebSocket] = []
        for ws in targets:
            try:
                await ws.send_json(payload)
                delivered += 1
            except Exception:
                dead.append(ws)

        if dead:
            async with self._lock:
                sockets = self._connections.get(user_id)
                if sockets is not None:
                    for ws in dead:
                        sockets.discard(ws)
                    if not sockets:
                        self._connections.pop(user_id, None)
        return delivered

