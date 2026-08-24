"""In-app messaging, REST half (FR 2.2).

The WebSocket half lives in ``app/ws/chat.py``; both write through
:func:`persist_message` in this module. That sharing is the point. The required
sequence is "persist the message, commit, then broadcast", and a second
copy of that sequence in the socket handler is how the two paths quietly drift
into producing different rows, different frames, or -- worst -- a socket path
that broadcasts before it commits.

Three decisions worth knowing before editing:

* **The socket is never the only delivery path.** Every send commits a row and
  the push is best effort on top, so a client that reconnects re-pulls history
  from ``GET /conversations/{id}/messages`` and misses nothing.
* **Reading a thread is a write.** Opening a thread marks the *other* party's
  messages read, which is what makes the inbox badge mean anything. It also
  pushes a read receipt so the sender's open tab updates without polling.
* **NEW_MESSAGE notifications are coalesced per thread.** One row per unread
  conversation, not per message: a 40-message argument must leave one entry in
  the bell menu, not forty. The next notification for that thread is written
  only once the previous one has been read.

Each send is its own short transaction. SQLite has one writer, and holding a
session open for the life of a socket is the fastest way to make chat block
every upload in the process.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.deps import CurrentUser, SessionDep
from app.core.util import utcnow
from app.models import (
    Conversation,
    Message,
    Notification,
    NotificationType,
    StudyMaterial,
    User,
)
from app.schemas import (
    ConversationCreate,
    ConversationRead,
    MessageCreate,
    MessageRead,
    MessageResponse,
    Page,
)
from app.services import notifier
from app.ws.manager import manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/conversations", tags=["chat"])

#: The inbox shows a one-line teaser, never the whole message.
PREVIEW_CHARS = 120

#: Default page of history. Enough to fill a scrollback on open; the client
#: pages further back with ``before``.
DEFAULT_MESSAGE_LIMIT = 50
MAX_MESSAGE_LIMIT = 200


# ---------------------------------------------------------------------------
# participants
# ---------------------------------------------------------------------------


def other_participant(conversation: Conversation, user_id: str) -> str:
    """The id of whoever is *not* ``user_id``.

    The table stores a canonical ``user_a_id < user_b_id`` pair, which is what
    makes the UNIQUE index work and is useless to a client -- every screen
    wants "the person I am talking to".
    """
    return (
        conversation.user_b_id
        if conversation.user_a_id == user_id
        else conversation.user_a_id
    )


def is_participant(conversation: Conversation, user_id: str) -> bool:
    return user_id in (conversation.user_a_id, conversation.user_b_id)


async def get_conversation_or_404(
    session: AsyncSession, conversation_id: str
) -> Conversation:
    conversation = (
        await session.execute(
            select(Conversation).where(Conversation.conversation_id == conversation_id)
        )
    ).scalar_one_or_none()
    if conversation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That conversation no longer exists.",
        )
    return conversation


def assert_participant(conversation: Conversation, user: User) -> None:
    """403, not 404: the caller asked about a real thread that is not theirs."""
    if not is_participant(conversation, user.user_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not part of this conversation.",
        )


# ---------------------------------------------------------------------------
# serialisation & socket frames
# ---------------------------------------------------------------------------


def to_message_read(message: Message, sender_name: str | None) -> MessageRead:
    read = MessageRead.model_validate(message)
    read.sender_name = sender_name
    return read


def message_frame(message: Message, sender_name: str | None) -> dict[str, Any]:
    """The ``/ws/chat`` frame for one message.

    Mirrors ``MessageRead`` field for field so a pushed frame drops straight
    into the same list the REST history renders -- the client never needs a
    second shape, and a reconnect cannot produce two versions of one message.
    """
    return {
        "type": "message",
        "data": {
            "message_id": message.message_id,
            "conversation_id": message.conversation_id,
            "sender_id": message.sender_id,
            "sender_name": sender_name,
            "content": message.content,
            "timestamp": message.timestamp.isoformat(),
            "read_at": message.read_at.isoformat() if message.read_at else None,
        },
    }


def _read_frame(conversation_id: str, reader_id: str, read_at: datetime) -> dict[str, Any]:
    return {
        "type": "read",
        "data": {
            "conversation_id": conversation_id,
            "reader_id": reader_id,
            "read_at": read_at.isoformat(),
        },
    }


async def _push(user_ids: list[str], frame: dict[str, Any]) -> None:
    """Best effort, always. The row is already committed by the time we push."""
    try:
        await manager.broadcast_to_conversation(user_ids, frame)
    except Exception:  # noqa: BLE001 -- a dead tab must not fail a 201
        logger.debug("chat push failed for %s", user_ids, exc_info=True)


# ---------------------------------------------------------------------------
# the shared write path
# ---------------------------------------------------------------------------


async def persist_message(
    session: AsyncSession,
    conversation: Conversation,
    sender: User,
    content: str,
) -> Message:
    """Write one message, then announce it. Used by REST *and* ``/ws/chat``.

    Order is load-bearing: row, commit, push. The push touches two sockets that
    may both be gone, and neither outcome may change what is in the database.

    Callers do not need to broadcast afterwards -- both participants (including
    the sender's other tabs) are pushed here, so the socket handler that owns
    the sending tab has nothing left to do but await this.
    """
    body = content.strip()
    if not body:
        # The socket path has no pydantic validation in front of it, so the
        # emptiness rule has to live where both paths pass through.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="A message cannot be empty.",
        )
    if len(body) > 4000:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="A message cannot be longer than 4000 characters.",
        )

    now = utcnow()
    message = Message(
        conversation_id=conversation.conversation_id,
        sender_id=sender.user_id,
        content=body,
        timestamp=now,
    )
    session.add(message)

    # The inbox is ordered on this column, so a thread that does not bump it
    # sinks to the bottom no matter how active it is.
    conversation.last_message_at = now
    session.add(conversation)

    recipient_id = other_participant(conversation, sender.user_id)
    if await _needs_message_notification(session, conversation, recipient_id):
        await notifier.notify(
            session,
            user_id=recipient_id,
            type=NotificationType.NEW_MESSAGE,
            message=f"{sender.name} sent you a message.",
            ref_type="conversation",
            ref_id=conversation.conversation_id,
            commit=False,
        )

    await session.commit()
    await session.refresh(message)

    await _push(
        [conversation.user_a_id, conversation.user_b_id],
        message_frame(message, sender.name),
    )
    return message


async def _needs_message_notification(
    session: AsyncSession, conversation: Conversation, recipient_id: str
) -> bool:
    """True unless an unread NEW_MESSAGE for this thread already exists.

    One SELECT per message, on an indexed column, to avoid turning a
    conversation into a wall of identical bell entries.
    """
    existing = (
        await session.execute(
            select(Notification.notification_id)
            .where(
                Notification.user_id == recipient_id,
                Notification.type == NotificationType.NEW_MESSAGE.value,
                Notification.ref_type == "conversation",
                Notification.ref_id == conversation.conversation_id,
                Notification.is_read.is_(False),
            )
            .limit(1)
        )
    ).scalars().first()
    return existing is None


async def _mark_other_party_read(
    session: AsyncSession, conversation: Conversation, reader_id: str
) -> int:
    """Mark the counterparty's unread messages read; returns how many.

    One UPDATE rather than a read-modify-write loop: this runs on every open of
    every thread, and SQLite has one writer.
    """
    read_at = utcnow()
    result = await session.execute(
        update(Message)
        .where(
            Message.conversation_id == conversation.conversation_id,
            Message.sender_id != reader_id,
            Message.read_at.is_(None),
        )
        .values(read_at=read_at)
    )
    marked = int(result.rowcount or 0)
    if marked:
        await session.commit()
        await _push(
            [other_participant(conversation, reader_id)],
            _read_frame(conversation.conversation_id, reader_id, read_at),
        )
    return marked


# ---------------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------------


@router.get("", response_model=list[ConversationRead], summary="List your threads")
async def list_conversations(
    session: SessionDep, current_user: CurrentUser
) -> list[ConversationRead]:
    """The inbox: most recent first, each with its own unread count.

    The count and the preview are correlated sub-selects, so the whole inbox is
    one statement instead of one query per thread. Threads with no messages
    yet sort last -- SQLite orders NULL below everything under DESC, which is
    exactly where an empty thread belongs.
    """
    me = current_user.user_id

    unread_count = (
        select(func.count(Message.message_id))
        .where(
            Message.conversation_id == Conversation.conversation_id,
            Message.sender_id != me,
            Message.read_at.is_(None),
        )
        .correlate(Conversation)
        .scalar_subquery()
    )
    last_content = (
        select(Message.content)
        .where(Message.conversation_id == Conversation.conversation_id)
        .order_by(Message.timestamp.desc())
        .limit(1)
        .correlate(Conversation)
        .scalar_subquery()
    )

    stmt = (
        select(Conversation, unread_count, last_content)
        .where((Conversation.user_a_id == me) | (Conversation.user_b_id == me))
        .order_by(Conversation.last_message_at.desc(), Conversation.created_at.desc())
    )
    rows = (await session.execute(stmt)).all()
    if not rows:
        return []

    conversations = [row[0] for row in rows]
    names = await _user_names(
        session, {other_participant(c, me) for c in conversations}
    )
    titles = await _listing_titles(
        session, {c.listing_id for c in conversations if c.listing_id}
    )

    return [
        ConversationRead(
            conversation_id=conversation.conversation_id,
            other_user_id=other_participant(conversation, me),
            other_user_name=names.get(other_participant(conversation, me)),
            listing_id=conversation.listing_id,
            listing_title=titles.get(conversation.listing_id or ""),
            last_message_at=conversation.last_message_at,
            last_message_preview=_preview(preview),
            unread_count=int(unread or 0),
        )
        for conversation, unread, preview in rows
    ]


@router.post("", response_model=ConversationRead, summary="Open or reuse a thread")
async def create_conversation(
    payload: ConversationCreate,
    session: SessionDep,
    current_user: CurrentUser,
) -> ConversationRead:
    """Get-or-create, keyed on the canonical pair plus the listing.

    Canonicalising to ``user_a_id < user_b_id`` before the lookup is what makes
    "A messages B" and "B messages A" the same thread rather than two.
    """
    if payload.other_user_id == current_user.user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot start a conversation with yourself.",
        )

    other = (
        await session.execute(
            select(User).where(User.user_id == payload.other_user_id)
        )
    ).scalar_one_or_none()
    if other is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That user does not exist.",
        )

    listing_title: str | None = None
    if payload.listing_id:
        listing_title = (
            await session.execute(
                select(StudyMaterial.title).where(
                    StudyMaterial.listing_id == payload.listing_id
                )
            )
        ).scalars().first()
        if listing_title is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="That listing no longer exists.",
            )

    user_a, user_b = sorted((current_user.user_id, other.user_id))
    conversation = await _find_thread(session, user_a, user_b, payload.listing_id)

    if conversation is None:
        conversation = Conversation(
            user_a_id=user_a,
            user_b_id=user_b,
            listing_id=payload.listing_id,
            created_at=utcnow(),
        )
        session.add(conversation)
        try:
            await session.commit()
        except IntegrityError:
            # Two tabs opened the same thread at once. The UNIQUE index did its
            # job; the loser just reuses the winner's row.
            await session.rollback()
            conversation = await _find_thread(
                session, user_a, user_b, payload.listing_id
            )
            if conversation is None:
                raise
        else:
            await session.refresh(conversation)

    return ConversationRead(
        conversation_id=conversation.conversation_id,
        other_user_id=other.user_id,
        other_user_name=other.name,
        listing_id=conversation.listing_id,
        listing_title=listing_title,
        last_message_at=conversation.last_message_at,
        unread_count=0,
    )


@router.get(
    "/{conversation_id}/messages",
    response_model=Page[MessageRead],
    summary="Thread history",
)
async def list_messages(
    conversation_id: str,
    session: SessionDep,
    current_user: CurrentUser,
    before: Annotated[datetime | None, Query()] = None,
    limit: Annotated[int | None, Query(ge=1, le=MAX_MESSAGE_LIMIT)] = None,
    page_size: Annotated[int | None, Query(ge=1, le=MAX_MESSAGE_LIMIT)] = None,
) -> Page[MessageRead]:
    """Newest ``limit`` messages, returned oldest-first for rendering.

    ``before`` is a timestamp cursor rather than a page number: scrolling back
    through a thread that is receiving messages would otherwise re-show rows as
    the offsets shift underneath. ``page_size`` is accepted as an alias so a
    client using the generic paged hook still gets the page it asked for.

    Opening the thread marks the other party's messages read -- a GET with a
    deliberate side effect, because "I have seen these" is precisely what
    fetching them means.
    """
    conversation = await get_conversation_or_404(session, conversation_id)
    assert_participant(conversation, current_user)

    effective_limit = limit or page_size or DEFAULT_MESSAGE_LIMIT

    total = int(
        (
            await session.execute(
                select(func.count())
                .select_from(Message)
                .where(Message.conversation_id == conversation_id)
            )
        ).scalar_one()
    )

    criteria = [Message.conversation_id == conversation_id]
    if before is not None:
        criteria.append(Message.timestamp < before)

    stmt = (
        select(Message, User.name)
        .join(User, User.user_id == Message.sender_id)
        .where(*criteria)
        # Newest first so the limit takes the *end* of the thread, then
        # reversed below -- a chat renders oldest at the top.
        .order_by(Message.timestamp.desc())
        .limit(effective_limit)
    )
    rows = list((await session.execute(stmt)).all())
    rows.reverse()

    await _mark_other_party_read(session, conversation, current_user.user_id)

    items = [to_message_read(message, sender_name) for message, sender_name in rows]
    return Page.build(items, total, 1, effective_limit)


@router.post(
    "/{conversation_id}/messages",
    response_model=MessageRead,
    status_code=status.HTTP_201_CREATED,
    summary="Send a message",
)
async def send_message(
    conversation_id: str,
    payload: MessageCreate,
    session: SessionDep,
    current_user: CurrentUser,
) -> MessageRead:
    """The fallback for when the socket is down.

    Identical in effect to a socket send -- same row, same push, same
    notification -- because both call :func:`persist_message`.
    """
    conversation = await get_conversation_or_404(session, conversation_id)
    assert_participant(conversation, current_user)

    message = await persist_message(
        session, conversation, current_user, payload.content
    )
    return to_message_read(message, current_user.name)


@router.post(
    "/{conversation_id}/read",
    response_model=MessageResponse,
    summary="Mark a thread read",
)
async def mark_conversation_read(
    conversation_id: str, session: SessionDep, current_user: CurrentUser
) -> MessageResponse:
    """Clear the badge without re-fetching history.

    The client already has the messages from the socket; this is the cheap way
    to say "the user is looking at them".
    """
    conversation = await get_conversation_or_404(session, conversation_id)
    assert_participant(conversation, current_user)

    marked = await _mark_other_party_read(session, conversation, current_user.user_id)
    return MessageResponse(detail=f"Marked {marked} message(s) as read.")


# ---------------------------------------------------------------------------
# lookups
# ---------------------------------------------------------------------------


async def _find_thread(
    session: AsyncSession, user_a: str, user_b: str, listing_id: str | None
) -> Conversation | None:
    """The UNIQUE (a, b, listing_id) row, if it exists.

    ``listing_id`` needs the IS NULL form rather than ``== None``: a
    listing-less thread is a distinct thread, not a wildcard.
    """
    criteria = [Conversation.user_a_id == user_a, Conversation.user_b_id == user_b]
    criteria.append(
        Conversation.listing_id.is_(None)
        if listing_id is None
        else Conversation.listing_id == listing_id
    )
    return (
        await session.execute(select(Conversation).where(*criteria))
    ).scalar_one_or_none()


async def _user_names(session: AsyncSession, user_ids: set[str]) -> dict[str, str]:
    """One query for every counterparty in the inbox, not one per row."""
    if not user_ids:
        return {}
    rows = (
        await session.execute(
            select(User.user_id, User.name).where(User.user_id.in_(list(user_ids)))
        )
    ).all()
    return {user_id: name for user_id, name in rows}


async def _listing_titles(session: AsyncSession, listing_ids: set[str]) -> dict[str, str]:
    if not listing_ids:
        return {}
    rows = (
        await session.execute(
            select(StudyMaterial.listing_id, StudyMaterial.title).where(
                StudyMaterial.listing_id.in_(list(listing_ids))
            )
        )
    ).all()
    return {listing_id: title for listing_id, title in rows}


def _preview(content: str | None) -> str | None:
    if not content:
        return None
    collapsed = " ".join(content.split())
    if len(collapsed) <= PREVIEW_CHARS:
        return collapsed
    return collapsed[: PREVIEW_CHARS - 1].rstrip() + "…"


__all__ = [
    "assert_participant",
    "get_conversation_or_404",
    "is_participant",
    "message_frame",
    "other_participant",
    "persist_message",
    "router",
    "to_message_read",
]
