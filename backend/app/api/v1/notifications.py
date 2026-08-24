"""The durable notification list behind the bell (FR 2.3, FR 3.2).

Notifications are persisted first and pushed over the socket second, which
makes these routes the *primary* delivery
path rather than a fallback: a rental reminder or an auto-match fires precisely
when the recipient is not looking at the app, and a socket frame nobody was
connected to receive is simply lost. Everything the bell menu shows on login
comes from here.

The writes delegate to ``services.notifier`` -- the same functions the
WebSocket layer and the scheduled job use -- so "mark read" means one thing in
one place. Only the paged read is built here, because a single filtered SELECT
over one table is not business logic.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Query
from sqlalchemy import func
from sqlmodel import select

from app.core.deps import CurrentUser, SessionDep
from app.models import Notification
from app.schemas import MessageResponse, NotificationRead, ORMModel, Page
from app.services import notifier

router = APIRouter(tags=["notifications"])


class UnreadCountResponse(ORMModel):
    """Local to this router on purpose.

    A one-field envelope for the badge poll; no other module shares it, and
    putting it in ``schemas`` would imply a contract that does not exist.
    """

    count: int


@router.get(
    "/notifications",
    response_model=Page[NotificationRead],
    summary="List your notifications",
)
async def list_notifications(
    session: SessionDep,
    current_user: CurrentUser,
    unread_only: Annotated[
        bool, Query(description="Return only notifications you have not read yet")
    ] = False,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Page[NotificationRead]:
    # The user filter is not optional anywhere in this router: there is no
    # parameter for whose notifications to read, only the token's subject.
    conditions = [Notification.user_id == current_user.user_id]
    if unread_only:
        conditions.append(Notification.is_read.is_(False))

    total = int(
        (
            await session.execute(
                select(func.count()).select_from(Notification).where(*conditions)
            )
        ).scalar_one()
    )

    stmt = (
        select(Notification)
        .where(*conditions)
        # notify_many stamps a whole batch with one timestamp, so the id is the
        # tie-break that keeps page 2 from repeating a row from page 1.
        .order_by(Notification.created_at.desc(), Notification.notification_id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = (await session.execute(stmt)).scalars().all()

    items = [NotificationRead.model_validate(row) for row in rows]
    return Page[NotificationRead].build(
        items=items, total=total, page=page, page_size=page_size
    )


@router.get(
    "/notifications/unread-count",
    response_model=UnreadCountResponse,
    summary="Count your unread notifications",
)
async def unread_count(
    session: SessionDep, current_user: CurrentUser
) -> UnreadCountResponse:
    # Polled by the badge whenever the socket is down, so it stays a COUNT
    # covered by idx_notif_user rather than a page fetched and measured.
    return UnreadCountResponse(
        count=await notifier.unread_count(session, current_user.user_id)
    )


@router.post(
    "/notifications/{notification_id}/read",
    response_model=MessageResponse,
    summary="Mark one notification as read",
)
async def mark_notification_read(
    notification_id: Annotated[str, Path(description="Id of the notification to clear")],
    session: SessionDep,
    current_user: CurrentUser,
) -> MessageResponse:
    # notifier.mark_read owns the ownership check: 404 when the row is gone,
    # 403 when it belongs to somebody else, and a no-op when it was already
    # read so a double-click is not an error.
    await notifier.mark_read(session, current_user.user_id, notification_id)
    return MessageResponse(detail="Notification marked as read.")


@router.post(
    "/notifications/read-all",
    response_model=MessageResponse,
    summary="Mark all of your notifications as read",
)
async def mark_all_notifications_read(
    session: SessionDep, current_user: CurrentUser
) -> MessageResponse:
    cleared = await notifier.mark_all_read(session, current_user.user_id)
    # Report the count: "0 cleared" is the honest answer when the badge was
    # already empty, and it is what makes a stale badge diagnosable.
    return MessageResponse(
        detail=f"{cleared} notification{'' if cleared == 1 else 's'} marked as read."
    )


__all__ = ["router"]
