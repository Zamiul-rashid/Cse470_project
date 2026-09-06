"""``/ws/chat`` -- the live half of in-app messaging (FR 2.2).

The socket is an *upgrade*, never the record. Every frame it accepts is written
through :func:`app.api.v1.chat.persist_message` -- the same function the REST
``POST /conversations/{id}/messages`` path calls -- so a message that arrives
over the socket and one that arrives over HTTP are byte-for-byte the same row,
raise the same errors and fire the same NEW_MESSAGE notification. If this
endpoint were to write its own row the two paths would drift, and the drift
would only show up on demo day.

Three constraints shape everything below:

* **Auth happens before ``accept()``.** Browsers cannot set headers on a
  WebSocket upgrade, so the access token rides in the query string.
* **The session is ours, the transactions are short.** SQLite allows one
  writer at a time; a session left mid-transaction while we block on
  ``receive()`` is exactly how "database is locked" happens under a demo with
  two browsers open.
* **A bad frame is not a bad connection.** Malformed JSON, an unknown
  conversation, an empty body -- all answered with an error frame. Dropping the
  socket would cost the user their whole thread over one typo, and the client
  would reconnect anyway.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Protocol

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.api.v1.chat import persist_message
from app.core.db import SessionLocal
from app.core.deps import load_user_from_token
from app.models import Conversation, User
from app.schemas.community import MessageCreate
from app.ws.manager import manager

logger = logging.getLogger(__name__)

router = APIRouter()

#: Close codes 4000-4999 are the application-private range (RFC 6455 s7.4.2);
#: 4401 mirrors HTTP 401 so the client can tell "your token is not valid" from
#: "the network dropped". frontend/src/lib/ws.ts stops its exponential-backoff
#: reconnect on exactly this code -- retrying a rejected token forever is just
#: a slow denial of service against your own server.
WS_UNAUTHORIZED = 4401


class _Persisted(Protocol):
    """What this module needs back from ``persist_message``.

    Satisfied by both the ``Message`` row and its ``MessageRead`` schema, so
    the frame builder does not care which one the shared writer hands back.
    """

    message_id: str
    conversation_id: str
    sender_id: str
    content: str
    timestamp: datetime
    read_at: datetime | None


# ---------------------------------------------------------------------------
# frames
# ---------------------------------------------------------------------------


def _message_frame(message: _Persisted, sender_name: str | None) -> dict[str, Any]:
    """One saved message, shaped for both frame readers.

    ``kind``/``message`` matches ``services.notifier`` (the other thing that
    pushes through the manager); ``type``/``data`` matches ``ChatSocketEvent``
    in frontend/src/lib/types.ts. Carrying both keys in one dict costs a few
    bytes and saves the two halves of the app from disagreeing about an
    envelope -- the payload underneath is ``MessageRead`` either way.
    """
    payload = {
        "message_id": message.message_id,
        "conversation_id": message.conversation_id,
        "sender_id": message.sender_id,
        "sender_name": sender_name,
        "content": message.content,
        "timestamp": message.timestamp.isoformat(),
        "read_at": message.read_at.isoformat() if message.read_at else None,
    }
    return {"kind": "message", "message": payload, "type": "message", "data": payload}


def _error_frame(detail: str) -> dict[str, Any]:
    return {"kind": "error", "detail": detail, "type": "error", "data": {"detail": detail}}


async def _send(websocket: WebSocket, payload: dict[str, Any]) -> None:
    """Best effort, like every other push in this codebase.

    The message is already committed by the time we get here, so a send that
    fails costs the user nothing the REST history will not return.
    """
    try:
        await websocket.send_json(payload)
    except Exception:  # noqa: BLE001 -- a dead socket is not an error condition
        logger.debug("chat frame dropped: socket gone", exc_info=True)


# ---------------------------------------------------------------------------
# one frame
# ---------------------------------------------------------------------------


async def _handle_frame(
    session: AsyncSession, websocket: WebSocket, sender: User, raw: str
) -> None:
    """Decode, authorise, persist, deliver. Never raises for client mistakes."""
    try:
        frame = json.loads(raw)
    except ValueError:
        await _send(websocket, _error_frame("That frame was not valid JSON."))
        return
    if not isinstance(frame, dict):
        await _send(websocket, _error_frame("A chat frame must be a JSON object."))
        return

    conversation_id = frame.get("conversation_id")
    if not isinstance(conversation_id, str) or not conversation_id:
        await _send(websocket, _error_frame("A chat frame needs a conversation_id."))
        return

    try:
        body = MessageCreate(content=frame.get("content") or "")
    except ValidationError as exc:
        detail = str(exc.errors()[0].get("msg", "That message could not be sent."))
        await _send(websocket, _error_frame(detail.removeprefix("Value error, ")))
        return

    conversation = (
        await session.execute(
            select(Conversation).where(Conversation.conversation_id == conversation_id)
        )
    ).scalar_one_or_none()
    if conversation is None:
        await _send(websocket, _error_frame("That conversation no longer exists."))
        return

    # Re-checked on *every* frame, not once at connect. The conversation id is
    # client-supplied and the socket outlives any single authorisation, so a
    # cached or guessed id must never be trusted -- this is the only thing
    # standing between a stranger and someone else's thread.
    participants = (conversation.user_a_id, conversation.user_b_id)
    if sender.user_id not in participants:
        await _send(websocket, _error_frame("This conversation is not yours."))
        return

    try:
        message = await persist_message(
            session,
            conversation=conversation,
            sender=sender,
            content=body.content,
        )
    except HTTPException as exc:
        # The shared writer speaks HTTP because its other caller is a router;
        # translate rather than let a 409 kill the socket.
        await _send(websocket, _error_frame(str(exc.detail)))
        return

    frame_out = _message_frame(message, sender.name)

    # Echo down *this* socket first: the sender's own tab must see its message
    # land whether or not the registry still holds a reference to it.
    await _send(websocket, frame_out)

    recipient_id = (
        conversation.user_b_id
        if sender.user_id == conversation.user_a_id
        else conversation.user_a_id
    )
    await manager.send_to_user(recipient_id, frame_out)


# ---------------------------------------------------------------------------
# the endpoint
# ---------------------------------------------------------------------------


@router.websocket("/ws/chat")
async def chat_socket(websocket: WebSocket) -> None:
    """Live send/receive for FR 2.2. Token in the query string, see above."""
    token = websocket.query_params.get("token")

    # Its own session, for the life of the socket: a request-scoped dependency
    # would be closed the moment the handshake finished.
    async with SessionLocal() as session:
        if not token:
            await websocket.close(code=WS_UNAUTHORIZED)
            return
        try:
            user = await load_user_from_token(session, token)
        except HTTPException:
            # Before accept(): an unauthenticated client never gets an open
            # socket, only the 4401 that tells it to stop retrying.
            await websocket.close(code=WS_UNAUTHORIZED)
            return

        await websocket.accept()
        await manager.connect(user.user_id, websocket)
        logger.debug("chat socket open for %s", user.user_id)

        try:
            while True:
                try:
                    raw = await websocket.receive_text()
                except (KeyError, TypeError):
                    # A binary frame -- starlette has no text to hand us.
                    await _send(websocket, _error_frame("Chat frames must be JSON text."))
                    continue

                try:
                    await _handle_frame(session, websocket, user, raw)
                finally:
                    # End whatever implicit transaction the frame opened before
                    # we block on receive() again. Holding a SQLite transaction
                    # across an idle socket is the fastest route to a locked
                    # database.
                    await session.rollback()
        except WebSocketDisconnect:
            pass
        except Exception:  # noqa: BLE001 -- one socket must not take the app down
            logger.exception("chat socket failed for %s", user.user_id)
        finally:
            # Also runs on the exception path. A socket left in the registry is
            # a push into a black hole for every later message.
            await manager.disconnect(user.user_id, websocket)


__all__ = ["WS_UNAUTHORIZED", "chat_socket", "router"]
