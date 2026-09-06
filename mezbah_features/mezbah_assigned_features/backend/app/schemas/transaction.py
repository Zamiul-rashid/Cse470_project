"""Module 3 shapes: transactions, rentals, QR handoff, reports.

``TransactionRead`` is assembled, not dumped. Three of its fields exist only
because the client would otherwise re-derive them and get them wrong:

* ``role`` -- "am I the buyer or the seller here?" decides the entire UI, and
  the client should not be comparing ids to work it out.
* ``handoff`` -- a flattened view of ``qr_handoffs`` with no token or hash in
  it. The raw token appears exactly once, in ``QRGenerateResponse``, to the
  seller who asked for it.
* ``can_review`` -- encodes the FR 2.5 gate (transaction COMPLETED, caller is
  a party, caller has not already reviewed) so the review button is never
  offered only to 409 on click.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.models import ListingType, ReportReason, ReportStatus, TransactionStatus
from app.schemas.common import ORMModel

# ---------------------------------------------------------------------------
# transactions (FR 3.4)
# ---------------------------------------------------------------------------


class TransactionCreate(BaseModel):
    """Buyer initiates. Seller, type and default price come from the listing.

    ``agreed_price`` is optional because the parties may have negotiated in
    chat; when omitted the listing price stands. ``rental_days`` is only read
    for RENT listings and sets the due date when the handoff completes.
    """

    listing_id: str
    agreed_price: float | None = Field(default=None, ge=0)
    rental_days: int | None = Field(default=None, ge=1, le=365)


class TransactionStatusUpdate(BaseModel):
    """The single entry point to the state machine.

    Legality is decided by ``TRANSACTION_TRANSITIONS`` in the service layer,
    not here -- a schema cannot see the current status, and an illegal move
    must be a 409 rather than a 422.
    """

    status: TransactionStatus


class RentalRead(ORMModel):
    """``days_remaining`` and ``is_overdue`` are computed against ``utcnow()``.

    Both are server-side so every client agrees on which day it is -- a
    browser in another timezone must not decide a book is late.
    """

    rental_id: str
    transaction_id: str
    listing_title: str | None = None
    start_date: datetime
    due_date: datetime
    returned: bool = False
    returned_at: datetime | None = None
    #: Negative once overdue.
    days_remaining: int
    is_overdue: bool


class HandoffState(ORMModel):
    """What the transaction page needs to know about the QR step.

    Note what is absent: ``token_hash``, ``nonce`` and ``manual_code``. This
    object is shown to both parties, and the manual code is a credential.
    """

    generated: bool = False
    expires_at: datetime | None = None
    verified_by_buyer: bool = False
    verified_by_seller: bool = False
    verified_at: datetime | None = None


class TransactionRead(ORMModel):
    transaction_id: str
    listing_id: str
    listing_title: str | None = None
    buyer_id: str
    buyer_name: str | None = None
    seller_id: str
    seller_name: str | None = None

    transaction_type: ListingType
    agreed_price: float | None = None
    status: TransactionStatus
    transaction_date: datetime
    completed_at: datetime | None = None

    #: Resolved per caller, never stored.
    role: Literal["buyer", "seller"]
    rental: RentalRead | None = None
    handoff: HandoffState | None = None
    can_review: bool = False


# ---------------------------------------------------------------------------
# QR handoff (FR 3.3)
# ---------------------------------------------------------------------------


class QRGenerateResponse(ORMModel):
    """Returned to the seller only, once, at generation time.

    The PNG is a ``data:`` URI so the code never becomes a fetchable URL that
    a third party could pull. ``manual_code`` is the typed fallback for when
    the camera is blocked outside a secure context.
    ``token`` is the signed value the buyer's scanner posts back; the server
    keeps only its hash.
    """

    qr_png_data_uri: str
    manual_code: str
    expires_at: datetime
    token: str


class QRVerifyRequest(BaseModel):
    """Either branch of the handoff: scanned token, or typed 6-digit code."""

    token: str | None = None
    manual_code: str | None = None

    @model_validator(mode="after")
    def _one_of(self) -> QRVerifyRequest:
        if not self.token and not self.manual_code:
            raise ValueError("Scan the QR code or enter the 6-digit code.")
        return self


# ---------------------------------------------------------------------------
# reports (FR 3.5)
# ---------------------------------------------------------------------------


class ReportCreate(BaseModel):
    listing_id: str
    reason: ReportReason
    details: str | None = Field(default=None, max_length=2000)


class ReportRead(ORMModel):
    report_id: str
    listing_id: str
    listing_title: str | None = None
    reporter_id: str
    reporter_name: str | None = None
    reason: ReportReason
    details: str | None = None
    status: ReportStatus
    created_at: datetime
    resolved_at: datetime | None = None
    resolution_note: str | None = None


__all__ = [
    "HandoffState",
    "QRGenerateResponse",
    "QRVerifyRequest",
    "RentalRead",
    "ReportCreate",
    "ReportRead",
    "TransactionCreate",
    "TransactionRead",
    "TransactionStatusUpdate",
]
