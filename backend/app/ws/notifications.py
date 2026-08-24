"""``/ws/notifications`` -- server push only (FR 2.3, FR 3.2).

There is deliberately no client protocol here. Everything a user can *do* with
a notification (list them, mark one read, clear the badge) is a REST call with
a durable result; the socket exists purely so the bell moves without a poll.
Giving it a write path would mean two ways to mutate the same rows, one of them
unauthenticated against CSRF-style replay and none of them retryable.

The frames the client receives are produced elsewhere -- ``services.notifier``
pushes through the same :data:`~app.ws.manager.manager` after it has committed
the row. That ordering is the whole design: a user
who is offline when a rental falls due still finds the notification waiting on
next login, because the push was never the delivery mechanism.

This module therefore does exactly three things: authenticate the handshake,
register the socket, and send the initial unread count so the badge is correct
the instant the page loads rather than one notification later.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from app.core.db import SessionLocal
from app.core.deps import load_user_from_token
from app.services.notifier import unread_count
from app.ws.manager import manager

logger = logging.getLogger(__name__)

router = APIRouter()

#: See app/ws/chat.py -- 4000-4999 is the application-private close range and
#: 4401 mirrors HTTP 401, which is what stops the client reconnecting forever
#: with a token the server has already rejected.
WS_UNAUTHORIZED = 4401


def _unread_frame(count: int) -> dict[str, Any]:
    """Both envelopes in one dict; see ``ws.chat._message_frame`` for why.

    ``kind``/``count`` is the backend convention (``services.notifier`` pushes
    ``kind``); ``type``/``data`` is ``NotificationSocketEvent`` in
    frontend/src/lib/types.ts.
    """
    return {
        "kind": "unread_count",
        "count": count,
        "type": "unread_count",
        "data": {"unread_count": count},
    }


@router.websocket("/ws/notifications")
async def notifications_socket(websocket: WebSocket) -> None:
    """Push-only socket. Reads client frames only to notice a disconnect."""
    token = websocket.query_params.get("token")

    # Our own session rather than a request-scoped dependency, which would be
    # closed as soon as the handshake completed.
    async with SessionLocal() as session:
        if not token:
            await websocket.close(code=WS_UNAUTHORIZED)
            return
        try:
            user = await load_user_from_token(session, token)
        except HTTPException:
            # Validated *before* accept(): browsers cannot set an
            # Authorization header on a WebSocket upgrade, so the token comes
            # in the query string and this is the only place to reject it.
            await websocket.close(code=WS_UNAUTHORIZED)
            return

        await websocket.accept()
        await manager.connect(user.user_id, websocket)

        try:
            count = await unread_count(session, user.user_id)
            # End the read transaction immediately. Nothing below touches the
            # database, and an idle session holding a SQLite transaction open
            # for the life of a socket is what blocks every other writer.
            await session.rollback()
            await websocket.send_json(_unread_frame(count))

            while True:
                # A push-only socket still has to await receive(): it is the
                # only way starlette surfaces the client going away, and
                # without it a closed tab would linger in the registry until
                # the next failed send. Whatever arrives is discarded.
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    break
        except WebSocketDisconnect:
            pass
        except Exception:  # noqa: BLE001 -- one dead tab must not take the app down
            logger.exception("notification socket failed for %s", user.user_id)
        finally:
            # Always, including the exception path: a stale entry here means
            # every later push for this user goes into a black hole.
            await manager.disconnect(user.user_id, websocket)


__all__ = ["WS_UNAUTHORIZED", "notifications_socket", "router"]
