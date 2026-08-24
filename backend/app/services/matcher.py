"""Auto-match: turn an approved listing into notifications (FR 2.3).

Two decisions fix the shape of auto-match, including why the wishlist cannot
drive it. Read them before changing anything here:

* **Requests drive matching, not wishlists.** A wishlist bookmarks listings
  that already exist, so it can never match a listing created tomorrow. A
  ``Request`` is the forward-looking saved search, and its criteria columns
  exist precisely so this module has something to match on.
* **The trigger is approval, not creation.** A PENDING listing is invisible to
  everyone but its uploader and the moderators, so notifying about it sends
  people to a 404.

:func:`match_wishlist_watchers` is a deliberately weaker second signal: someone
who bookmarked *a different* listing for the same course has shown interest in
that course, which is worth one nudge but is not a saved search. It is capped
and de-duplicated against the request matches so nobody gets two alerts for the
same listing.

Both functions are safe to call from a FastAPI ``BackgroundTask``, which is how
the admin's approve click returns immediately.
"""

from __future__ import annotations

import logging

from sqlalchemy import or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.util import normalize_edition
from app.models import (
    ListingStatus,
    Notification,
    NotificationType,
    Request,
    RequestStatus,
    StudyMaterial,
    Wishlist,
    WishlistItem,
)
from app.services import notifier

logger = logging.getLogger(__name__)

#: Wishlist watchers are a soft signal, and one popular course can have
#: hundreds of them. Cap the fan-out so approving a listing stays a fast,
#: bounded unit of work.
WISHLIST_WATCHER_CAP = 20

#: Notification types this module writes. Used to detect a re-run: approving a
#: listing twice (or a retried background task) must not re-notify.
_MATCH_TYPES = (NotificationType.REQUEST_MATCH, NotificationType.WISHLIST_MATCH)


async def _already_notified(session: AsyncSession, listing_id: str) -> set[str]:
    """Users who already have a match notification for this listing.

    Reading it back from ``notifications`` rather than threading state between
    the two functions is what makes the cap "one notification per user per
    listing" hold across separate calls and across a process restart.
    """
    stmt = select(Notification.user_id).where(
        Notification.ref_type == "listing",
        Notification.ref_id == listing_id,
        Notification.type.in_([t.value for t in _MATCH_TYPES]),
    )
    return set((await session.execute(stmt)).scalars().all())


def _edition_matches(request_edition: str | None, listing_edition: str | None) -> bool:
    """An unspecified edition matches anything; otherwise compare normalised.

    This cannot be pushed into SQL -- ``normalize_edition`` strips case, spaces
    and ordinal punctuation so ``3rd Ed.`` and ``3RDED`` are the same edition,
    and SQLite has no equivalent expression. The candidate set is already
    narrowed to one course code by then, so the Python pass is over a handful
    of rows.
    """
    if not request_edition:
        return True
    return normalize_edition(request_edition) == normalize_edition(listing_edition)


async def match_listing(session: AsyncSession, listing: StudyMaterial) -> int:
    """Notify every open request this newly approved listing satisfies.

    Matched requests move OPEN -> MATCHED, so the board stops advertising a
    want that has been answered and the same request never fires twice.

    Returns the number of requesters notified.
    """
    # Guard rather than assert: assertions vanish under ``python -O``, and
    # notifying about a listing nobody can open is the exact failure mode
    # this guard exists to prevent.
    if listing.status != ListingStatus.APPROVED:
        logger.debug(
            "match_listing skipped: listing %s is %s, not APPROVED",
            listing.listing_id,
            listing.status,
        )
        return 0
    if not listing.course_code_norm:
        return 0

    criteria = [
        Request.status == RequestStatus.OPEN,
        Request.course_code_norm == listing.course_code_norm,
        # Never tell someone their own upload answered their own request.
        Request.requester_id != listing.uploader_id,
        # A NULL criterion means "don't care", so it matches everything.
        or_(Request.department.is_(None), Request.department == listing.department),
        or_(
            Request.listing_type.is_(None),
            Request.listing_type == listing.listing_type,
        ),
        or_(Request.campus_id.is_(None), Request.campus_id == listing.campus_id),
    ]
    if listing.price is not None:
        # A priced listing must fit the budget; an EXCHANGE/FREE listing has no
        # price to compare, and a free book is inside every budget.
        criteria.append(
            or_(Request.max_price.is_(None), Request.max_price >= listing.price)
        )

    candidates = (
        (await session.execute(select(Request).where(*criteria))).scalars().all()
    )
    if not candidates:
        return 0

    already = await _already_notified(session, listing.listing_id)
    message = (
        f"A listing matching your request for {listing.course_code} is now "
        f"available: {listing.title}"
    )

    notified = 0
    for request in candidates:
        if not _edition_matches(request.edition, listing.edition):
            continue
        # Two open requests from the same user for the same course must still
        # produce one alert, and ``already`` is only refreshed from the
        # database at the top -- so track the ids we add here as we go.
        if request.requester_id in already:
            # Still resolve the request: it *was* answered by this listing.
            request.status = RequestStatus.MATCHED
            session.add(request)
            continue

        await notifier.notify(
            session,
            user_id=request.requester_id,
            type=NotificationType.REQUEST_MATCH,
            message=message,
            ref_type="listing",
            ref_id=listing.listing_id,
            commit=False,
        )
        already.add(request.requester_id)
        request.status = RequestStatus.MATCHED
        session.add(request)
        notified += 1

    # One commit for the notifications *and* the status changes: a requester
    # never sees an alert for a request the board still shows as OPEN.
    await session.commit()
    return notified


async def match_wishlist_watchers(session: AsyncSession, listing: StudyMaterial) -> int:
    """Nudge users who bookmarked another listing for the same course.

    Cheaper and weaker than :func:`match_listing`: no criteria beyond the
    course code, capped at :data:`WISHLIST_WATCHER_CAP` users, and it changes
    no state. Call it *after* ``match_listing`` -- it skips anyone that call
    already notified.

    Returns the number of watchers notified.
    """
    if listing.status != ListingStatus.APPROVED or not listing.course_code_norm:
        return 0

    already = await _already_notified(session, listing.listing_id)
    already.add(listing.uploader_id)

    stmt = (
        select(Wishlist.user_id)
        .join(WishlistItem, WishlistItem.wishlist_id == Wishlist.wishlist_id)
        .join(StudyMaterial, StudyMaterial.listing_id == WishlistItem.listing_id)
        .where(
            StudyMaterial.course_code_norm == listing.course_code_norm,
            # "another listing" -- bookmarking this one would notify a user
            # about something they already have.
            StudyMaterial.listing_id != listing.listing_id,
            Wishlist.user_id.notin_(list(already)),
        )
        .distinct()
        .limit(WISHLIST_WATCHER_CAP)
    )
    watchers = list((await session.execute(stmt)).scalars().all())
    if not watchers:
        return 0

    await notifier.notify_many(
        session,
        watchers,
        type=NotificationType.WISHLIST_MATCH,
        message=(
            f"New {listing.course_code} material you might want: {listing.title}"
        ),
        ref_type="listing",
        ref_id=listing.listing_id,
        commit=True,
    )
    return len(watchers)


async def run_for_listing(session: AsyncSession, listing: StudyMaterial) -> int:
    """Both passes in the right order; what the approval endpoint schedules.

    Ordering matters: the request matches are written first so the wishlist
    pass can see them and skip those users.
    """
    matched = await match_listing(session, listing)
    watched = await match_wishlist_watchers(session, listing)
    return matched + watched


__all__ = [
    "WISHLIST_WATCHER_CAP",
    "match_listing",
    "match_wishlist_watchers",
    "run_for_listing",
]
