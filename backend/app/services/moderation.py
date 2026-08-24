"""Admin moderation of listings (FR 4.1).

Approving is not a one-row update. It flips the listing visible, tells the
uploader, and fires the auto-match that notifies everyone with an open request
for that course (FR 2.3) -- three aggregates, so it belongs in a service rather
than in the router.

Auto-match runs on **approval**, never on creation: a PENDING listing is
invisible, so notifying about it would send users to a page they cannot open.

The three decisions are deliberately separate functions rather than one
``moderate(action=...)``. Reject demands a note and approve does not, remove is
reachable from any status while the other two are PENDING-only, and only
approve triggers the matcher -- a single function would be three branches
sharing nothing but a signature.
"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.util import utcnow
from app.models import ListingStatus, NotificationType, StudyMaterial, User
from app.services.notifier import notify


def _assert_pending(listing: StudyMaterial, verb: str) -> None:
    """Approve and reject are decisions on a *pending* listing.

    Re-approving an already-approved listing would re-run the matcher and
    re-notify every requester, so this guard is what stops a double-click
    spamming the board.
    """
    if listing.status != ListingStatus.PENDING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Only pending listings can be {verb}. This one is already "
                f"{listing.status}."
            ),
        )


def _stamp(listing: StudyMaterial, admin: User, note: str | None) -> None:
    """Who decided, when, and why -- the audit trail behind every takedown."""
    listing.moderated_by = admin.user_id
    listing.moderated_at = utcnow()
    listing.moderation_note = note


async def approve_listing(
    session: AsyncSession,
    listing: StudyMaterial,
    admin: User,
    note: str | None = None,
) -> int:
    """Publish the listing, then auto-match it. Returns the notifications sent.

    The count is returned so the router can report "3 students were notified"
    instead of a bare 200 -- the single most convincing moment in the FR 2.3
    demo, and otherwise invisible to the admin who caused it.
    """
    _assert_pending(listing, "approved")

    listing.status = ListingStatus.APPROVED
    _stamp(listing, admin, note)
    session.add(listing)
    await session.commit()
    await session.refresh(listing)

    await notify(
        session,
        user_id=listing.uploader_id,
        type=NotificationType.MODERATION_RESULT,
        message=f"Your listing '{listing.title}' was approved and is now visible.",
        ref_type="listing",
        ref_id=listing.listing_id,
    )

    # Imported here, not at module scope: the matcher notifies about listings
    # and this module approves them, so a top-level import is a cycle.
    from app.services.matcher import match_listing

    matched = await match_listing(session, listing)
    return matched if isinstance(matched, int) else len(matched)


async def reject_listing(
    session: AsyncSession,
    listing: StudyMaterial,
    admin: User,
    note: str,
) -> StudyMaterial:
    """Rejection without a reason is indistinguishable from the site being broken.

    The note is required here rather than in the schema because approve and
    reject share one request body (see schemas/admin.py).
    """
    if not note or not note.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="A rejection note is required -- the uploader is shown this reason.",
        )
    _assert_pending(listing, "rejected")

    listing.status = ListingStatus.REJECTED
    _stamp(listing, admin, note.strip())
    session.add(listing)
    await session.commit()
    await session.refresh(listing)

    await notify(
        session,
        user_id=listing.uploader_id,
        type=NotificationType.MODERATION_RESULT,
        message=f"Your listing '{listing.title}' was not approved: {listing.moderation_note}",
        ref_type="listing",
        ref_id=listing.listing_id,
    )
    return listing


async def remove_listing(
    session: AsyncSession,
    listing: StudyMaterial,
    admin: User,
    note: str | None = None,
) -> StudyMaterial:
    """Takedown from any status -- this is the notice-and-takedown half of the
    copyright answer, so it must reach a listing
    that has already been approved, reserved or completed."""
    if listing.status == ListingStatus.REMOVED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This listing has already been removed.",
        )

    listing.status = ListingStatus.REMOVED
    _stamp(listing, admin, note)
    session.add(listing)
    await session.commit()
    await session.refresh(listing)

    await notify(
        session,
        user_id=listing.uploader_id,
        type=NotificationType.MODERATION_RESULT,
        message=(
            f"Your listing '{listing.title}' was removed by a moderator."
            + (f" Reason: {note}" if note else "")
        ),
        ref_type="listing",
        ref_id=listing.listing_id,
    )
    return listing


__all__ = ["approve_listing", "reject_listing", "remove_listing"]
