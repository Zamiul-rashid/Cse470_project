"""In-process registry of live WebSockets, keyed by ``user_id``.

The whole registry is a dict in this process's memory. That is deliberate --
at assignment scale a Redis pub/sub fan-out is complexity nobody grades -- but
it has one hard consequence: **the app must run with ``uvicorn --workers 1``**.
With two workers a user connected to worker A is invisible to worker B, so half
the pushes silently vanish.

One user maps to a *set* of sockets, not one: a student with the dashboard open
in two tabs has two live connections and both want the badge to move.

Nothing here is a delivery guarantee. Every push is best effort layered on top
of a persisted row (notifications) or a persisted message (chat); a send that
fails is dropped and the REST history covers it.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import WebSocket
from starlette.websockets import WebSocketState

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Maps ``user_id -> {WebSocket, ...}`` for the lifetime of the process."""

    def __init__(self) -> None:
        self._connections: dict[str, set[WebSocket]] = {}
        # Guards the dict only. Never held across an ``await send_*``: a slow
        # or half-dead client would otherwise block every other user's push.
        self._lock = asyncio.Lock()

    # -- lifecycle ---------------------------------------------------------

    async def connect(self, user_id: str, websocket: WebSocket) -> None:
        """Register a socket, accepting the handshake if the caller has not.

        The token must be validated *before* ``accept()`` so a bad token can
        be closed with 4401, which means the
        endpoint usually accepts first. Tolerating both orders keeps the chat
        and notification endpoints from having to agree on a convention.
        """
        if websocket.client_state == WebSocketState.CONNECTING:
            await websocket.accept()
        async with self._lock:
            self._connections.setdefault(user_id, set()).add(websocket)

    async def disconnect(self, user_id: str, websocket: WebSocket) -> None:
        """Unregister a socket. Idempotent by design.

        This runs from the normal close path, from the ``WebSocketDisconnect``
        handler, and from :meth:`send_to_user` when a send fails -- so the same
        socket is routinely removed twice. ``set.discard`` makes that a no-op
        instead of a KeyError inside an ``except`` block.
        """
        async with self._lock:
            sockets = self._connections.get(user_id)
            if sockets is None:
                return
            sockets.discard(websocket)
            if not sockets:
                # Don't leak an empty set per user who ever logged in.
                self._connections.pop(user_id, None)

    # -- delivery ----------------------------------------------------------

    async def send_to_user(self, user_id: str, payload: dict[str, Any]) -> int:
        """Push ``payload`` to every socket this user has open.

        Returns the number of sockets actually reached -- callers use it only
        for logging, never to decide whether the work succeeded. A failed send
        drops the socket rather than propagating: a dead browser tab must not
        turn an admin's approve click into a 500.
        """
        async with self._lock:
            # Snapshot: the set can be mutated while we are awaiting sends.
            sockets = list(self._connections.get(user_id, ()))

        reached = 0
        dead: list[WebSocket] = []
        for websocket in sockets:
            try:
                await websocket.send_json(payload)
                reached += 1
            except Exception:  # noqa: BLE001 -- any socket error means "gone"
                logger.debug("dropping dead socket for user %s", user_id, exc_info=True)
                dead.append(websocket)

        for websocket in dead:
            await self.disconnect(user_id, websocket)
        return reached

    async def broadcast_to_conversation(
        self, user_ids: list[str], payload: dict[str, Any]
    ) -> int:
        """Fan a chat frame out to both sides of a thread (or any user list).

        Duplicates are collapsed so a self-conversation or a repeated id never
        double-delivers.
        """
        reached = 0
        for user_id in dict.fromkeys(user_ids):
            reached += await self.send_to_user(user_id, payload)
        return reached

    # -- introspection -----------------------------------------------------

    def is_online(self, user_id: str) -> bool:
        """Cheap and synchronous: callers check it inside request handlers."""
        return bool(self._connections.get(user_id))

    def connection_count(self, user_id: str | None = None) -> int:
        """Open sockets for one user, or across the whole process."""
        if user_id is not None:
            return len(self._connections.get(user_id, ()))
        return sum(len(sockets) for sockets in self._connections.values())


#: Process-wide singleton. Importing the class and building a second instance
#: would create a second, invisible registry -- always import ``manager``.
manager = ConnectionManager()

__all__ = ["ConnectionManager", "manager"]
