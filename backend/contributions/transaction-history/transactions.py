"""Transactions and QR handoff (FR 3.3, 3.4).

This router does not contain the state machine -- it contains the HTTP around
it. Every write to ``transactions.status`` goes through
``services/transactions.py``, and completion goes through ``services/qr.py``,
which is what makes the "every illegal transition is a 409, not a 500" test
mean anything: a second writer here would silently
route around the guard. Nothing below assigns ``.status``.

Everything returns through ``_transaction_read``. A transaction row on its own
is close to unusable by a client -- it holds two user ids and a listing id, and
the page needs names, the rental term, whether a handoff code exists and
whether the review button should be shown. Assembling that in one place is what
stops three endpoints drifting into three slightly different answers.

The assembly is batched (``_load_context``) because ``GET /transactions/me``
returns a page of them and a naive helper would issue five queries per row.
The one thing deliberately *not* batched is ``can_review``: the FR 2.5 gate
lives in the service so the button and ``POST /reviews`` can never disagree,
and duplicating it here to save a query would be the exact drift that gate
exists to prevent.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, HTTPException, Query, status
from pydantic import ValidationError
from sqlalchemy import func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.api.v1.rentals import rental_read
from app.core.deps import CurrentUser, SessionDep
from app.models import (
    QRHandoff,
    Rental,
    StudyMaterial,
    Transaction,
    TransactionStatus,
    User,
    UserRole,
)
from app.schemas import (
    HandoffState,
    Page,
    QRGenerateResponse,
    QRVerifyRequest,
    TransactionCreate,
    TransactionRead,
    TransactionStatusUpdate,
)
from app.services import qr as qr_service
from app.services import transactions as transaction_service

router = APIRouter(prefix="/transactions", tags=["transactions"])


# ---------------------------------------------------------------------------
# response assembly
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _Context:
    """Everything a page of transactions needs, fetched once instead of per row."""

    listing_titles: dict[str, str]
    user_names: dict[str, str]
    rentals: dict[str, Rental]
    handoffs: dict[str, QRHandoff]


async def _load_context(
    session: AsyncSession, transactions: Sequence[Transaction]
) -> _Context:
    if not transactions:
        return _Context({}, {}, {}, {})

    listing_ids = {t.listing_id for t in transactions}
    user_ids = {t.buyer_id for t in transactions} | {t.seller_id for t in transactions}
    transaction_ids = [t.transaction_id for t in transactions]

    titles = dict(
        (
            await session.execute(
                select(StudyMaterial.listing_id, StudyMaterial.title).where(
                    StudyMaterial.listing_id.in_(listing_ids)
                )
            )
        ).all()
    )
    names = dict(
        (
            await session.execute(
                select(User.user_id, User.name).where(User.user_id.in_(user_ids))
            )
        ).all()
    )
    rentals = {
        rental.transaction_id: rental
        for rental in (
            await session.execute(
                select(Rental).where(Rental.transaction_id.in_(transaction_ids))
            )
        )
        .scalars()
        .all()
    }
    handoffs = {
        handoff.transaction_id: handoff
        for handoff in (
            await session.execute(
                select(QRHandoff).where(QRHandoff.transaction_id.in_(transaction_ids))
            )
        )
        .scalars()
        .all()
    }
    return _Context(titles, names, rentals, handoffs)


def _role_for(transaction: Transaction, viewer: User) -> Literal["buyer", "seller"]:
    """Which seat the viewer is in.

    There are only two seats, so an admin looking at somebody else's
    transaction is shown the seller's view. That is a display default, not a
    grant: an observer is read-only everywhere else in this router, and
    ``can_review`` is false for them because the service checks party
    membership rather than this field.
    """
    return "buyer" if viewer.user_id == transaction.buyer_id else "seller"


def _handoff_state(handoff: QRHandoff | None) -> HandoffState:
    """Always an object, never null -- ``generated`` is the flag to branch on.

    Note what does not cross the wire: ``token_hash``, ``nonce`` and
    ``manual_code``. Both parties see this object, and the manual code is a
    credential that only its holder may read.
    """
    if handoff is None:
        return HandoffState(generated=False)
    return HandoffState(
        generated=True,
        expires_at=handoff.expires_at,
        verified_by_buyer=handoff.verified_by_buyer,
        verified_by_seller=handoff.verified_by_seller,
        verified_at=handoff.verified_at,
    )


async def _transaction_read(
    session: AsyncSession,
    transaction: Transaction,
    viewer: User,
    context: _Context | None = None,
) -> TransactionRead:
    """The single shape every endpoint in this module returns."""
    ctx = context if context is not None else await _load_context(session, [transaction])

    title = ctx.listing_titles.get(transaction.listing_id)
    rental = ctx.rentals.get(transaction.transaction_id)

    return TransactionRead(
        transaction_id=transaction.transaction_id,
        listing_id=transaction.listing_id,
        listing_title=title,
        buyer_id=transaction.buyer_id,
        buyer_name=ctx.user_names.get(transaction.buyer_id),
        seller_id=transaction.seller_id,
        seller_name=ctx.user_names.get(transaction.seller_id),
        transaction_type=transaction.transaction_type,
        agreed_price=transaction.agreed_price,
        status=transaction.status,
        transaction_date=transaction.transaction_date,
        completed_at=transaction.completed_at,
        role=_role_for(transaction, viewer),
        rental=rental_read(rental, title) if rental is not None else None,
        handoff=_handoff_state(ctx.handoffs.get(transaction.transaction_id)),
        can_review=await transaction_service.can_review(
            session, transaction, viewer.user_id
        ),
    )


async def _get_listing_or_404(session: AsyncSession, listing_id: str) -> StudyMaterial:
    listing = (
        await session.execute(
            select(StudyMaterial).where(StudyMaterial.listing_id == listing_id)
        )
    ).scalar_one_or_none()
    if listing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That listing does not exist.",
        )
    return listing


# ---------------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------------


@router.post(
    "",
    response_model=TransactionRead,
    status_code=status.HTTP_201_CREATED,
    summary="Request a listing",
)
async def create_transaction(
    payload: TransactionCreate,
    session: SessionDep,
    current_user: CurrentUser,
) -> TransactionRead:
    """The buyer initiates. Availability, price and the rental term are the
    service's business -- this only resolves the listing and hands over."""
    listing = await _get_listing_or_404(session, payload.listing_id)
    transaction = await transaction_service.create_transaction(
        session,
        buyer=current_user,
        listing=listing,
        agreed_price=payload.agreed_price,
        rental_days=payload.rental_days,
    )
    return await _transaction_read(session, transaction, current_user)


@router.get(
    "/me",
    response_model=Page[TransactionRead],
    summary="My transaction history",
)
async def list_my_transactions(
    session: SessionDep,
    current_user: CurrentUser,
    role: Annotated[
        Literal["buyer", "seller", "all"],
        Query(description="Which side of the deal to list."),
    ] = "all",
    status_filter: Annotated[
        TransactionStatus | None,
        Query(alias="status", description="Restrict to one status."),
    ] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Page[TransactionRead]:
    """Buys, sells, rentals and exchanges in one list (FR 3.4).

    Declared before ``/{transaction_id}`` because FastAPI matches routes in
    declaration order and ``me`` is a valid-looking id.
    """
    conditions: list[Any] = []
    if role == "buyer":
        conditions.append(Transaction.buyer_id == current_user.user_id)
    elif role == "seller":
        conditions.append(Transaction.seller_id == current_user.user_id)
    else:
        conditions.append(
            or_(
                Transaction.buyer_id == current_user.user_id,
                Transaction.seller_id == current_user.user_id,
            )
        )
    if status_filter is not None:
        conditions.append(Transaction.status == status_filter.value)

    total = int(
        (
            await session.execute(
                select(func.count()).select_from(Transaction).where(*conditions)
            )
        ).scalar_one()
    )
    rows = (
        (
            await session.execute(
                select(Transaction)
                .where(*conditions)
                .order_by(Transaction.transaction_date.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )

    context = await _load_context(session, rows)
    items = [
        await _transaction_read(session, transaction, current_user, context)
        for transaction in rows
    ]
    return Page.build(items, total, page, page_size)


@router.get(
    "/{transaction_id}",
    response_model=TransactionRead,
    summary="Transaction detail",
)
async def get_transaction(
    transaction_id: str,
    session: SessionDep,
    current_user: CurrentUser,
) -> TransactionRead:
    """Participants, plus admins.

    Admins can read it because a report or a dispute about a handoff is
    unanswerable otherwise; they still cannot move it, since every write below
    goes through a service that checks party membership.
    """
    transaction = await transaction_service.get_transaction_or_404(
        session, transaction_id
    )
    if current_user.role != UserRole.ADMIN:
        await transaction_service.assert_participant(transaction, current_user)
    return await _transaction_read(session, transaction, current_user)


@router.patch(
    "/{transaction_id}/status",
    response_model=TransactionRead,
    summary="Move the transaction",
)
async def update_transaction_status(
    transaction_id: str,
    payload: TransactionStatusUpdate,
    session: SessionDep,
    current_user: CurrentUser,
) -> TransactionRead:
    """The single guarded entry point to the state machine (FR 3.4).

    Legality, who may make which move, and the 409 on an illegal one all live
    in the service. ``COMPLETED`` is rejected there by design -- it is only
    reachable through the QR handoff below.
    """
    transaction = await transaction_service.get_transaction_or_404(
        session, transaction_id
    )
    transaction = await transaction_service.transition(
        session, transaction, current_user, payload.status.value
    )
    return await _transaction_read(session, transaction, current_user)


@router.post(
    "/{transaction_id}/qr",
    response_model=QRGenerateResponse,
    summary="Generate the handoff code",
)
async def generate_handoff_code(
    transaction_id: str,
    session: SessionDep,
    current_user: CurrentUser,
) -> QRGenerateResponse:
    """Seller only. The raw token exists in this response and nowhere else.

    The server keeps only its hash, so this body is the one and only copy --
    which is also why the PNG is an inline ``data:`` URI rather than a URL
    somebody could fetch a second time.
    """
    transaction = await transaction_service.get_transaction_or_404(
        session, transaction_id
    )
    # Party check first so a stranger gets "not yours" rather than the seller's
    # more specific "only the seller can do this", which confirms the id exists.
    await transaction_service.assert_participant(transaction, current_user)

    handoff, raw_token, png_data_uri = await qr_service.generate_handoff(
        session, transaction, current_user
    )
    return QRGenerateResponse(
        qr_png_data_uri=png_data_uri,
        manual_code=handoff.manual_code,
        expires_at=handoff.expires_at,
        token=raw_token,
    )


def _parse_verify_body(payload: dict[str, Any] | None) -> QRVerifyRequest:
    """Validate the buyer's half of the body by hand.

    ``QRVerifyRequest`` requires a token or a manual code, which is right for
    the buyer and wrong for the seller: the seller confirms on the device that
    generated the code and posts ``{}``. Declaring the body as the model would
    422 that empty body before the endpoint ever sees who is calling, so the
    body arrives loose and the buyer's branch validates it here -- with the
    model's own message, not a second copy of it.
    """
    try:
        return QRVerifyRequest.model_validate(payload or {})
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Scan the QR code or enter the 6-digit code.",
        ) from exc


@router.post(
    "/{transaction_id}/qr/verify",
    response_model=TransactionRead,
    summary="Confirm the handoff",
)
async def verify_handoff_code(
    transaction_id: str,
    session: SessionDep,
    current_user: CurrentUser,
    payload: Annotated[
        dict[str, Any] | None,
        Body(
            description=(
                "Buyer: {'token': ...} from the scanner, or "
                "{'manual_code': '123456'}. Seller: {}."
            )
        ),
    ] = None,
) -> TransactionRead:
    """One call per party; the second one completes the transaction (FR 3.3).

    The buyer proves possession of the code. The seller is already holding the
    screen that shows it, so confirming from their own account is proof enough.
    Completion itself is the service's -- there is no way to reach ``COMPLETED``
    from here without both flags set.
    """
    transaction = await transaction_service.get_transaction_or_404(
        session, transaction_id
    )
    role = await transaction_service.assert_participant(transaction, current_user)

    token: str | None = None
    manual_code: str | None = None
    if role == "buyer":
        body = _parse_verify_body(payload)
        token, manual_code = body.token, body.manual_code

    await qr_service.verify_handoff(
        session,
        transaction,
        current_user,
        token=token,
        manual_code=manual_code,
    )
    return await _transaction_read(session, transaction, current_user)


__all__ = ["router"]
