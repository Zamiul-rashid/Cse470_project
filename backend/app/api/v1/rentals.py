"""Rental tracking (FR 3.2).

The read side of the rental loop. The write side -- creating the row, rebasing
its clock onto the physical handoff -- belongs to
``services/transactions.py``; the reminder job in ``jobs/`` owns the T-3 / T-1 /
overdue notifications. What is left here is the two things a person does with a
loan: look at what they are holding, and say they gave it back.

``days_remaining`` and ``is_overdue`` are computed here rather than in the
browser because a client in another timezone must not get a vote on whether a
book is late. They are counted in *calendar days*, the same unit the reminder
job buckets on, so the page and the notification never disagree about how many
days are left.

``rental_read`` is exported: ``api/v1/transactions.py`` embeds the same shape
inside ``TransactionRead`` and there must be exactly one definition of what
"3 days remaining" means.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.deps import CurrentUser, SessionDep
from app.core.util import utcnow
from app.models import (
    NotificationType,
    Rental,
    StudyMaterial,
    Transaction,
    TransactionStatus,
    User,
)
from app.schemas import RentalRead
from app.services import transactions as transaction_service
from app.services.notifier import notify

router = APIRouter(prefix="/rentals", tags=["rentals"])


# ---------------------------------------------------------------------------
# shape
# ---------------------------------------------------------------------------


def rental_read(rental: Rental, listing_title: str | None = None) -> RentalRead:
    """Assemble the wire shape, with the countdown resolved server-side.

    A returned rental's countdown freezes at the moment it came back rather
    than ticking further into the negatives forever -- "returned 2 days early"
    stays true next month, "-47 days" does not.
    """
    reference = rental.returned_at if rental.returned and rental.returned_at else utcnow()
    return RentalRead(
        rental_id=rental.rental_id,
        transaction_id=rental.transaction_id,
        listing_title=listing_title,
        start_date=rental.start_date,
        due_date=rental.due_date,
        returned=rental.returned,
        returned_at=rental.returned_at,
        # Calendar days, not fractional days: "due in 1 day" must mean the same
        # thing here as it does in the reminder job's T_MINUS_1 bucket.
        days_remaining=(rental.due_date.date() - reference.date()).days,
        is_overdue=not rental.returned and utcnow() > rental.due_date,
    )


async def _get_rental_or_404(session: AsyncSession, rental_id: str) -> Rental:
    rental = (
        await session.execute(select(Rental).where(Rental.rental_id == rental_id))
    ).scalar_one_or_none()
    if rental is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That rental does not exist.",
        )
    return rental


async def _listing_title(session: AsyncSession, transaction: Transaction) -> str | None:
    return (
        await session.execute(
            select(StudyMaterial.title).where(
                StudyMaterial.listing_id == transaction.listing_id
            )
        )
    ).scalar_one_or_none()


# ---------------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------------


@router.get("/me", response_model=list[RentalRead], summary="My rentals")
async def list_my_rentals(
    session: SessionDep,
    current_user: CurrentUser,
    active_only: Annotated[
        bool,
        Query(description="Only rentals still out. Pass false for the full history."),
    ] = True,
) -> list[RentalRead]:
    """Both sides of the loan.

    The lender needs the due date as badly as the borrower does -- they are the
    one chasing it -- so this returns rentals where the caller is either party
    rather than borrowings only.

    Overdue rentals are *included* in ``active_only``: a book that is late is
    the most active rental a person has.
    """
    conditions = [
        or_(
            Transaction.buyer_id == current_user.user_id,
            Transaction.seller_id == current_user.user_id,
        )
    ]
    if active_only:
        conditions.append(Rental.returned.is_(False))

    stmt = (
        select(Rental, StudyMaterial.title)
        .join(Transaction, Transaction.transaction_id == Rental.transaction_id)
        .join(
            StudyMaterial,
            StudyMaterial.listing_id == Transaction.listing_id,
            isouter=True,
        )
        .where(*conditions)
        # Soonest due first: the page is a to-do list, not an archive.
        .order_by(Rental.due_date)
    )
    rows = (await session.execute(stmt)).all()
    return [rental_read(rental, title) for rental, title in rows]


@router.post(
    "/{rental_id}/return",
    response_model=RentalRead,
    summary="Mark a rental returned",
)
async def return_rental(
    rental_id: str,
    session: SessionDep,
    current_user: CurrentUser,
) -> RentalRead:
    """Either party may close a loan; the other one is told.

    Requiring both to confirm would strand rentals forever the moment one
    student stops opening the app, and there is no money at stake at this
    point -- the handoff already completed. Whoever acts, the counterparty gets
    a notification so a wrong claim is visible immediately.
    """
    rental = await _get_rental_or_404(session, rental_id)
    transaction = await transaction_service.get_transaction_or_404(
        session, rental.transaction_id
    )
    await transaction_service.assert_participant(transaction, current_user)

    # The rental row is written when the buyer states a term, long before the
    # book physically moves (see services/transactions.py). Nothing can be
    # given back that was never handed over.
    if transaction.status != TransactionStatus.COMPLETED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"This rental has not been handed over yet (the transaction is "
                f"{transaction.status}), so there is nothing to return."
            ),
        )
    if rental.returned:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This rental has already been marked returned.",
        )

    now = utcnow()
    days_late = (now.date() - rental.due_date.date()).days
    rental.returned = True
    rental.returned_at = now
    session.add(rental)
    await session.commit()
    await session.refresh(rental)

    title = await _listing_title(session, transaction) or "the item"
    counterparty = _counterparty(transaction, current_user)
    await notify(
        session,
        user_id=counterparty,
        # A return is a state change on the deal, not a reminder: RENTAL_DUE and
        # RENTAL_OVERDUE belong to the scheduler, and reusing one here would put
        # a "your rental is due" icon on a rental that just ended.
        type=NotificationType.TRANSACTION_UPDATE,
        message=(
            f"{current_user.name} marked '{title}' as returned."
            + (f" It was {days_late} day(s) late." if days_late > 0 else "")
        ),
        ref_type="transaction",
        ref_id=transaction.transaction_id,
    )
    return rental_read(rental, title)


def _counterparty(transaction: Transaction, actor: User) -> str:
    return (
        transaction.seller_id
        if actor.user_id == transaction.buyer_id
        else transaction.buyer_id
    )


__all__ = ["rental_read", "router"]
