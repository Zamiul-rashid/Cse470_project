"""Ratings and reviews (FR 2.5).

The whole requirement rests on one sentence in the SRS -- reviews happen
*after a transaction* -- so this router is mostly a gate. Four checks, in this
order, and none of them is optional:

1. the transaction exists (404),
2. it is COMPLETED (409) -- a handoff both parties confirmed is the only proof
   the two people ever met,
3. the caller is the buyer or the seller (403),
4. they have not reviewed it already (409, enforced again by
   ``UNIQUE (transaction_id, reviewer_id)`` in case two requests race).

The reviewee is **derived**, never read from the body. The client sends only a
transaction id; the other party to that transaction is who gets rated. Trusting
a ``reviewee_id`` from the wire would let anyone one-star a stranger, and no
amount of validation elsewhere would catch it.

``users.rating_avg`` / ``rating_count`` are recomputed from the table with a
single aggregate rather than nudged incrementally. An incremental average
drifts the first time a review is deleted or a transaction is voided, and it is
one query either way.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.deps import CurrentUser, SessionDep
from app.core.util import utcnow
from app.models import (
    NotificationType,
    Review,
    StudyMaterial,
    TransactionStatus,
    User,
)
from app.schemas import ReviewCreate, ReviewRead
from app.services import notifier
from app.services.transactions import get_transaction_or_404

router = APIRouter(prefix="/reviews", tags=["reviews"])


# ---------------------------------------------------------------------------
# rating recomputation
# ---------------------------------------------------------------------------


async def recompute_rating(session: AsyncSession, user_id: str) -> tuple[float, int]:
    """Rewrite one user's rating columns from ``reviews``. Returns (avg, count).

    Two decimal places because the profile renders "4.67" and storing more
    precision than anyone displays only invites two screens to round it
    differently.
    """
    average, count = (
        await session.execute(
            select(func.avg(Review.rating), func.count(Review.review_id)).where(
                Review.reviewee_id == user_id
            )
        )
    ).one()

    rating_avg = round(float(average), 2) if average is not None else 0.0
    rating_count = int(count or 0)

    user = (
        await session.execute(select(User).where(User.user_id == user_id))
    ).scalar_one_or_none()
    if user is not None:
        user.rating_avg = rating_avg
        user.rating_count = rating_count
        session.add(user)
    return rating_avg, rating_count


# ---------------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------------


@router.post(
    "",
    response_model=ReviewRead,
    status_code=status.HTTP_201_CREATED,
    summary="Review a completed transaction",
)
async def create_review(
    payload: ReviewCreate, session: SessionDep, current_user: CurrentUser
) -> ReviewRead:
    """Rate the other party to a transaction you completed."""
    transaction = await get_transaction_or_404(session, payload.transaction_id)

    if transaction.status != TransactionStatus.COMPLETED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"This transaction is {transaction.status}. You can leave a review "
                "once the handoff is confirmed by both sides."
            ),
        )
    if current_user.user_id not in (transaction.buyer_id, transaction.seller_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only review a transaction you took part in.",
        )

    already = (
        await session.execute(
            select(Review.review_id).where(
                Review.transaction_id == transaction.transaction_id,
                Review.reviewer_id == current_user.user_id,
            )
        )
    ).scalars().first()
    if already is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You have already reviewed this transaction.",
        )

    # Derived, never trusted from the body -- see the module docstring.
    reviewee_id = (
        transaction.seller_id
        if current_user.user_id == transaction.buyer_id
        else transaction.buyer_id
    )

    review = Review(
        transaction_id=transaction.transaction_id,
        reviewer_id=current_user.user_id,
        reviewee_id=reviewee_id,
        # The listing comes from the transaction too, so a review can never be
        # attached to material the two never exchanged.
        listing_id=transaction.listing_id,
        rating=payload.rating,
        comment=payload.comment,
        created_at=utcnow(),
    )
    session.add(review)
    # Flush before the aggregate: the new row has to be visible to the COUNT
    # and AVG that are about to read it.
    await session.flush()

    await recompute_rating(session, reviewee_id)
    await session.commit()
    await session.refresh(review)

    listing_title = await _listing_title(session, review.listing_id)
    await notifier.notify(
        session,
        user_id=reviewee_id,
        type=NotificationType.NEW_REVIEW,
        message=(
            f"{current_user.name} left you a {review.rating}-star review"
            + (f" for '{listing_title}'." if listing_title else ".")
        ),
        ref_type="transaction",
        ref_id=transaction.transaction_id,
    )

    return _to_read(review, current_user.name, listing_title)


@router.get("/{review_id}", response_model=ReviewRead, summary="Read one review")
async def get_review(review_id: str, session: SessionDep) -> ReviewRead:
    """Public: a rating nobody can open is not much of a reputation signal."""
    row = (
        await session.execute(
            select(Review, User.name, StudyMaterial.title)
            .join(User, User.user_id == Review.reviewer_id)
            # LEFT JOIN so a review survives its listing being removed.
            .outerjoin(StudyMaterial, StudyMaterial.listing_id == Review.listing_id)
            .where(Review.review_id == review_id)
        )
    ).first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That review does not exist.",
        )

    review, reviewer_name, listing_title = row
    return _to_read(review, reviewer_name, listing_title)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _to_read(
    review: Review, reviewer_name: str | None, listing_title: str | None
) -> ReviewRead:
    read = ReviewRead.model_validate(review)
    read.reviewer_name = reviewer_name
    read.listing_title = listing_title
    return read


async def _listing_title(session: AsyncSession, listing_id: str) -> str | None:
    return (
        await session.execute(
            select(StudyMaterial.title).where(StudyMaterial.listing_id == listing_id)
        )
    ).scalars().first()


__all__ = ["recompute_rating", "router"]
