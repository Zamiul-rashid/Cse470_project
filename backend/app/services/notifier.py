"""Notification delivery: persist first, push second.

That ordering is the rule, and everything in Phase 3 leans
on it. Every notification is a row in ``notifications``; the WebSocket push is
an optimisation layered on top. Invert the order and an offline user loses the
notification entirely -- FR 2.3 (auto-match) and FR 3.2 (rental reminders) both
fire at moments when the recipient is very likely *not* looking at the app.

The push therefore never affects the outcome of the request. A dead socket, a
client that closed mid-write, a serialisation error: all swallowed, because the
durable row already exists and ``GET /notifications`` will show it on next
login.

``commit`` exists because callers come in two shapes. A router that only wants
to notify passes ``commit=True`` and is done. A service in the middle of a unit
of work -- the matcher marking requests MATCHED, the transaction state machine
moving a status -- passes ``commit=False`` so the notification lands in the same
transaction as the change it announces, and commits once at the end.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from enum import Enum
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.util import utcnow
from app.models import Notification, NotificationType
from app.ws.manager import manager

logger = logging.getLogger(__name__)


def _text(value: NotificationType | str) -> str:
    """Store the enum's *value*, never ``str(member)``.

    On a ``class X(str, Enum)`` under Python 3.11, ``str(X.A)`` renders
    ``'X.A'`` -- so the naive conversion would write ``NotificationType.
    REQUEST_MATCH`` into the column and every client comparison would fail.
    """
    return value.value if isinstance(value, Enum) else value


def _frame(notification: Notification) -> dict[str, Any]:
    """The socket frame for one notification.

    Mirrors ``schemas.community.NotificationRead`` field for field so the
    client can feed a pushed frame into the same list it renders from REST,
    with no second shape to maintain.
    """
    return {
        "kind": "notification",
        "notification": {
            "notification_id": notification.notification_id,
            "type": notification.type,
            "message": notification.message,
            "ref_type": notification.ref_type,
            "ref_id": notification.ref_id,
            "is_read": notification.is_read,
            "created_at": notification.created_at.isoformat(),
        },
    }


async def _push(notification: Notification) -> None:
    """Best effort. Never raises -- see the module docstring."""
    try:
        await manager.send_to_user(notification.user_id, _frame(notification))
    except Exception:  # noqa: BLE001 -- delivery is optional, the row is not
        logger.debug(
            "websocket push failed for notification %s",
            notification.notification_id,
            exc_info=True,
        )


async def notify(
    session: AsyncSession,
    *,
    user_id: str,
    type: NotificationType | str,  # noqa: A002 -- matches notifications.type
    message: str,
    ref_type: str | None = None,
    ref_id: str | None = None,
    commit: bool = True,
) -> Notification:
    """Write one notification row, then try to push it.

    ``ref_type``/``ref_id`` are what the bell menu navigates on: pass
    ``'listing'``, ``'transaction'``, ``'conversation'`` or ``'request'`` with
    the matching id, or the notification is a dead end for the user.
    """
    notification = Notification(
        user_id=user_id,
        type=_text(type),
        message=message,
        ref_type=ref_type,
        ref_id=ref_id,
        created_at=utcnow(),
    )
    session.add(notification)
    # Row first, always. flush() is enough to make it real inside the caller's
    # transaction; commit() makes it real for everyone else.
    if commit:
        await session.commit()
    else:
        await session.flush()

    await _push(notification)
    return notification


async def notify_many(
    session: AsyncSession,
    user_ids: Iterable[str],
    *,
    type: NotificationType | str,  # noqa: A002 -- matches notifications.type
    message: str,
    ref_type: str | None = None,
    ref_id: str | None = None,
    commit: bool = True,
) -> list[Notification]:
    """Same body to several recipients, in one round trip to the database.

    Duplicate ids are collapsed: the matcher can legitimately hand the same
    user twice, and nobody wants the same alert twice.
    """
    unique_ids = list(dict.fromkeys(uid for uid in user_ids if uid))
    if not unique_ids:
        return []

    now = utcnow()
    notifications = [
        Notification(
            user_id=user_id,
            type=_text(type),
            message=message,
            ref_type=ref_type,
            ref_id=ref_id,
            created_at=now,
        )
        for user_id in unique_ids
    ]
    session.add_all(notifications)
    if commit:
        await session.commit()
    else:
        await session.flush()

    for notification in notifications:
        await _push(notification)
    return notifications


# ---------------------------------------------------------------------------
# read side
# ---------------------------------------------------------------------------


async def unread_count(session: AsyncSession, user_id: str) -> int:
    """Backs the badge on the bell. Covered by ``idx_notif_user``."""
    stmt = (
        select(func.count())
        .select_from(Notification)
        .where(Notification.user_id == user_id, Notification.is_read.is_(False))
    )
    return int((await session.execute(stmt)).scalar_one())


async def mark_read(
    session: AsyncSession, user_id: str, notification_id: str
) -> Notification:
    """404 if it does not exist, 403 if it belongs to somebody else.

    The two are kept distinct deliberately: the id is an opaque uuid4, so
    leaking existence tells an attacker nothing they could not already guess.
    """
    notification = (
        await session.execute(
            select(Notification).where(Notification.notification_id == notification_id)
        )
    ).scalar_one_or_none()

    if notification is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That notification no longer exists.",
        )
    if notification.user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only read your own notifications.",
        )

    if not notification.is_read:
        notification.is_read = True
        session.add(notification)
        await session.commit()
    return notification


async def mark_all_read(session: AsyncSession, user_id: str) -> int:
    """Clear the whole badge in one statement; returns how many were unread."""
    stmt = (
        update(Notification)
        .where(Notification.user_id == user_id, Notification.is_read.is_(False))
        .values(is_read=True)
    )
    result = await session.execute(stmt)
    await session.commit()
    return int(result.rowcount or 0)


__all__ = [
    "mark_all_read",
    "mark_read",
    "notify",
    "notify_many",
    "unread_count",
]
