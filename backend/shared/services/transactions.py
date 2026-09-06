"""The transaction state machine (FR 3.4).

This module is the **only** place in the codebase that assigns
``transactions.status``. Routers call ``transition()``; the QR service calls
``transition()`` and ``complete_transaction()``. Keeping every write behind one
door is what makes the parametrised "every illegal move returns 409, not 500"
test meaningful -- a second writer would silently
route around it.

Two decisions worth spelling out:

**Where ``rental_days`` lives.** ``transactions`` has no ``rental_days``
column and this module is not allowed to add one, so the rental term has to be
persisted the moment the buyer states it -- at ``REQUESTED`` -- or it is lost
before anyone can act on it. The ``rentals`` row itself is that storage: the
term is the span between ``start_date`` and ``due_date``. The row is written at
creation, and its clock is *rebased* onto ``utcnow()`` at ``ACCEPTED`` and
again at completion, so the due date always counts from the physical handoff
rather than from the moment the buyer clicked a button. ``returned`` stays
false throughout; a rental that never reached handoff is deleted with its
cancelled transaction.

**Completion is not a transition.** ``COMPLETED`` is rejected by
``transition()`` and reachable only through ``complete_transaction()``, which
the QR service calls once both parties have confirmed (FR 3.3). Otherwise
either party could mark a handoff done that never happened.
"""

from __future__ import annotations

from datetime import timedelta

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.util import utcnow
from app.models import (
    TRANSACTION_TRANSITIONS,
    ListingStatus,
    ListingType,
    NotificationType,
    Rental,
    Review,
    StudyMaterial,
    Transaction,
    TransactionStatus,
    User,
)

# The notifier is the shared delivery path: persist a row, then push over the
# socket if the recipient happens to be connected.
from app.services.notifier import notify

#: A campus rental is a semester at the outside. Wider than this is almost
#: always a typo, and the buyer is the one who eats an accidental 3650-day term.
MIN_RENTAL_DAYS = 1
MAX_RENTAL_DAYS = 90

_SECONDS_PER_DAY = 86_400


# ---------------------------------------------------------------------------
# lookups & guards
# ---------------------------------------------------------------------------


async def get_transaction_or_404(session: AsyncSession, transaction_id: str) -> Transaction:
    transaction = (
        await session.execute(
            select(Transaction).where(Transaction.transaction_id == transaction_id)
        )
    ).scalar_one_or_none()
    if transaction is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That transaction does not exist.",
        )
    return transaction


async def assert_participant(transaction: Transaction, user: User) -> str:
    """403 unless ``user`` is one of the two parties; returns their role.

    The role is returned rather than discarded because every caller needs it
    immediately afterwards -- "am I the buyer or the seller here?" decides both
    the permitted moves and the entire UI.
    """
    if user.user_id == transaction.buyer_id:
        return "buyer"
    if user.user_id == transaction.seller_id:
        return "seller"
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="This transaction is not yours.",
    )


async def _get_listing(session: AsyncSession, listing_id: str) -> StudyMaterial | None:
    return (
        await session.execute(
            select(StudyMaterial).where(StudyMaterial.listing_id == listing_id)
        )
    ).scalar_one_or_none()


async def _get_rental(session: AsyncSession, transaction_id: str) -> Rental | None:
    return (
        await session.execute(select(Rental).where(Rental.transaction_id == transaction_id))
    ).scalar_one_or_none()


# ---------------------------------------------------------------------------
# rentals -- see the module docstring for why the row is written this early
# ---------------------------------------------------------------------------


def _term_days(rental: Rental) -> int:
    """Recover the agreed term from the row that is storing it."""
    span = (rental.due_date - rental.start_date).total_seconds() / _SECONDS_PER_DAY
    return max(MIN_RENTAL_DAYS, round(span))


async def _restart_rental_clock(session: AsyncSession, transaction: Transaction) -> None:
    """Re-anchor an existing rental term on now, preserving its length."""
    rental = await _get_rental(session, transaction.transaction_id)
    if rental is None:
        return
    days = _term_days(rental)
    rental.start_date = utcnow()
    rental.due_date = rental.start_date + timedelta(days=days)
    session.add(rental)


# ---------------------------------------------------------------------------
# creation
# ---------------------------------------------------------------------------


async def create_transaction(
    session: AsyncSession,
    *,
    buyer: User,
    listing: StudyMaterial,
    agreed_price: float | None = None,
    rental_days: int | None = None,
) -> Transaction:
    """Buyer initiates. The listing is reserved so nobody else can start one.

    ``agreed_price`` is a parameter because the parties may have settled on a
    number in chat; when omitted the listing price stands. EXCHANGE and FREE
    force it back to NULL -- the DB CHECK constraint would reject anything else,
    and a "free" item with a price is a support ticket waiting to happen.
    """
    if listing.status != ListingStatus.APPROVED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"This listing is not available right now (it is {listing.status}). "
                "Only approved listings can be requested."
            ),
        )
    if buyer.user_id == listing.uploader_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You cannot start a transaction on your own listing.",
        )

    if listing.listing_type in (ListingType.EXCHANGE, ListingType.FREE):
        price: float | None = None
    else:
        price = listing.price if agreed_price is None else agreed_price
        if price is not None and price < 0:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="An agreed price cannot be negative.",
            )

    if listing.listing_type == ListingType.RENT and (
        rental_days is None or not MIN_RENTAL_DAYS <= rental_days <= MAX_RENTAL_DAYS
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "A rental needs a length between "
                f"{MIN_RENTAL_DAYS} and {MAX_RENTAL_DAYS} days."
            ),
        )

    transaction = Transaction(
        listing_id=listing.listing_id,
        buyer_id=buyer.user_id,
        seller_id=listing.uploader_id,
        transaction_type=listing.listing_type,
        agreed_price=price,
        status=TransactionStatus.REQUESTED,
        transaction_date=utcnow(),
    )
    session.add(transaction)

    # RESERVED, not COMPLETED: the listing stays visible so the buyer can still
    # open it, but no second buyer can start a competing transaction.
    listing.status = ListingStatus.RESERVED
    session.add(listing)

    if listing.listing_type == ListingType.RENT and rental_days is not None:
        # No relationship() ties Rental to Transaction, so the unit of work has
        # no dependency to sort on and flushes pending INSERTs in mapper-name
        # order -- 'Rental' before 'Transaction'. Under PRAGMA foreign_keys=ON
        # that is an immediate FOREIGN KEY failure, so the parent row has to be
        # written before the child is queued.
        await session.flush()
        started = utcnow()
        session.add(
            Rental(
                transaction_id=transaction.transaction_id,
                start_date=started,
                # Provisional: rebased at ACCEPTED and again at handoff.
                due_date=started + timedelta(days=rental_days),
            )
        )

    await session.commit()
    await session.refresh(transaction)

    await notify(
        session,
        user_id=transaction.seller_id,
        type=NotificationType.TRANSACTION_UPDATE,
        message=f"{buyer.name} wants your listing '{listing.title}'.",
        ref_type="transaction",
        ref_id=transaction.transaction_id,
    )
    return transaction


# ---------------------------------------------------------------------------
# the state machine
# ---------------------------------------------------------------------------


async def transition(
    session: AsyncSession,
    transaction: Transaction,
    actor: User,
    new_status: str,
) -> Transaction:
    """The single guarded entry point. Illegal moves are 409, never 500."""
    role = await assert_participant(transaction, actor)

    if new_status == TransactionStatus.COMPLETED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "A transaction cannot be marked completed directly. "
                "Complete it through the QR handoff, which both parties confirm."
            ),
        )

    allowed = TRANSACTION_TRANSITIONS.get(transaction.status, ())
    if new_status not in allowed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"A transaction cannot move from {transaction.status} to {new_status}. "
                + (
                    f"From {transaction.status} the only moves are: {', '.join(allowed)}."
                    if allowed
                    else f"{transaction.status} is a final state."
                )
            ),
        )

    if new_status == TransactionStatus.ACCEPTED and role != "seller":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the seller can accept a transaction request.",
        )

    transaction.status = new_status
    session.add(transaction)

    if new_status == TransactionStatus.ACCEPTED:
        # The deal is real now, so the rental term starts counting from here
        # rather than from whenever the request happened to be filed.
        await _restart_rental_clock(session, transaction)
    elif new_status == TransactionStatus.CANCELLED:
        await _release_listing(session, transaction)
        rental = await _get_rental(session, transaction.transaction_id)
        if rental is not None:
            # The term was only ever a carrier for the agreed length; with no
            # handoff there is nothing to be due back.
            await session.delete(rental)

    await session.commit()
    await session.refresh(transaction)

    await _notify_status_change(session, transaction, actor)
    return transaction


async def _release_listing(session: AsyncSession, transaction: Transaction) -> None:
    """Cancelling puts the listing back on the market -- unless it was pulled.

    A listing an admin has REMOVED must not quietly reappear because a buyer
    cancelled, so REMOVED is left alone.
    """
    listing = await _get_listing(session, transaction.listing_id)
    if listing is None or listing.status == ListingStatus.REMOVED:
        return
    if listing.status == ListingStatus.RESERVED:
        listing.status = ListingStatus.APPROVED
        session.add(listing)


async def _notify_status_change(
    session: AsyncSession, transaction: Transaction, actor: User
) -> None:
    """Tell the party who did *not* click the button."""
    counterparty = (
        transaction.seller_id
        if actor.user_id == transaction.buyer_id
        else transaction.buyer_id
    )
    listing = await _get_listing(session, transaction.listing_id)
    title = listing.title if listing is not None else "a listing"

    if transaction.status == TransactionStatus.ACCEPTED:
        message = f"Your request for '{title}' was accepted. Arrange the handoff."
    elif transaction.status == TransactionStatus.AWAITING_HANDOFF:
        message = f"'{title}' is ready for handoff -- scan the QR code to confirm."
    elif transaction.status == TransactionStatus.CANCELLED:
        message = f"The transaction for '{title}' was cancelled."
    else:
        return

    await notify(
        session,
        user_id=counterparty,
        type=NotificationType.TRANSACTION_UPDATE,
        message=message,
        ref_type="transaction",
        ref_id=transaction.transaction_id,
    )


# ---------------------------------------------------------------------------
# completion
# ---------------------------------------------------------------------------


async def complete_transaction(
    session: AsyncSession, transaction: Transaction
) -> Transaction:
    """Called by the QR service once both parties have confirmed (FR 3.3).

    Deliberately takes no ``actor``: there is no such thing as one party
    completing a handoff, so there is nobody to authorise. Authorisation
    happened when each side set their own verification flag.
    """
    if transaction.status == TransactionStatus.COMPLETED:
        # Idempotent: a duplicated verify request must not double-notify.
        return transaction
    if transaction.status == TransactionStatus.CANCELLED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This transaction was cancelled and can no longer be completed.",
        )

    transaction.status = TransactionStatus.COMPLETED
    transaction.completed_at = utcnow()
    session.add(transaction)

    listing = await _get_listing(session, transaction.listing_id)
    if listing is not None and listing.status != ListingStatus.REMOVED:
        listing.status = ListingStatus.COMPLETED
        session.add(listing)

    # The book changes hands now, so this is the honest start of the loan.
    if transaction.transaction_type == ListingType.RENT:
        await _restart_rental_clock(session, transaction)

    await session.commit()
    await session.refresh(transaction)

    title = listing.title if listing is not None else "the listing"
    for user_id in (transaction.buyer_id, transaction.seller_id):
        await notify(
            session,
            user_id=user_id,
            type=NotificationType.TRANSACTION_UPDATE,
            message=(
                f"Handoff confirmed for '{title}'. You can now leave a review."
            ),
            ref_type="transaction",
            ref_id=transaction.transaction_id,
        )
    return transaction


# ---------------------------------------------------------------------------
# review gate (FR 2.5)
# ---------------------------------------------------------------------------


async def can_review(
    session: AsyncSession, transaction: Transaction, user_id: str
) -> bool:
    """The FR 2.5 gate, in one place.

    Read by both ``TransactionRead.can_review`` and ``POST /reviews`` so the
    button is never offered only to 409 on click.
    """
    if transaction.status != TransactionStatus.COMPLETED:
        return False
    if user_id not in (transaction.buyer_id, transaction.seller_id):
        return False

    existing = (
        await session.execute(
            select(Review.review_id).where(
                Review.transaction_id == transaction.transaction_id,
                Review.reviewer_id == user_id,
            )
        )
    ).scalars().first()
    return existing is None


__all__ = [
    "MAX_RENTAL_DAYS",
    "MIN_RENTAL_DAYS",
    "assert_participant",
    "can_review",
    "complete_transaction",
    "create_transaction",
    "get_transaction_or_404",
    "transition",
]
