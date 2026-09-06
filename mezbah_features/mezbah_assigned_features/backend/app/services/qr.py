"""QR handoff that cannot be replayed or self-confirmed (FR 3.3).

A QR code carrying a bare transaction id fails the requirement in two ways: a
screenshot works forever, and either party can confirm a handoff that never
happened. So the token is *signed*, *short-lived* and *single-use*, and the
transaction only completes when both sides have confirmed independently.

The token is ``{transaction_id}:{nonce}:{exp}:{HMAC-SHA256 of the first three}``.
Everything needed to validate it is inside the token, but nothing in it is
trusted until the signature checks out, and the server keeps only
``sha256(token)`` -- a leaked database row cannot be turned back into a
scannable code.

The 6-digit ``manual_code`` is not a convenience. ``getUserMedia`` is blocked
outside a secure context, so the in-browser scanner silently dies the moment
the demo runs on ``http://192.168.x.x`` instead of ``localhost``. The typed
fallback is the only reason the feature
survives a badly lit room or a phone on the LAN.
"""

from __future__ import annotations

import base64
import io
import secrets
from datetime import datetime, timedelta, timezone

import qrcode
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.config import settings
from app.core.security import constant_time_equals, hash_token, sign_payload
from app.core.util import utcnow
from app.models import QRHandoff, Transaction, TransactionStatus, User
from app.services import transactions as transaction_service

#: Statuses from which a seller may raise a code. AWAITING_HANDOFF is included
#: so an expired or unscannable code can simply be regenerated.
_GENERATABLE = (TransactionStatus.ACCEPTED, TransactionStatus.AWAITING_HANDOFF)

_EXPIRED_DETAIL = "This code has expired. Ask the seller to generate a new one."
_STALE_DETAIL = "This code is no longer valid. Ask the seller to generate a new one."


# ---------------------------------------------------------------------------
# token encoding
# ---------------------------------------------------------------------------


def _epoch(moment: datetime) -> int:
    """``utcnow()`` is naive UTC, so state the timezone rather than letting
    ``.timestamp()`` guess -- on a machine set to Dhaka time that guess is six
    hours wrong, which is longer than the token's whole lifetime."""
    return int(moment.replace(tzinfo=timezone.utc).timestamp())


def _decode(raw_token: str) -> tuple[str, str, int]:
    """Return ``(transaction_id, nonce, exp)`` or raise 400.

    Verifies the signature *first*: none of the three values means anything
    until we know the server issued them.
    """
    parts = raw_token.strip().split(":")
    if len(parts) != 4:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="That is not a NoteVault handoff code. Scan the QR code again.",
        )

    transaction_id, nonce, exp_raw, signature = parts
    payload = f"{transaction_id}:{nonce}:{exp_raw}"
    if not constant_time_equals(signature, sign_payload(payload)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This code's signature is invalid. Ask the seller to show a fresh code.",
        )

    try:
        exp = int(exp_raw)
    except ValueError as exc:  # unreachable while the signature holds
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This code is malformed. Ask the seller to generate a new one.",
        ) from exc

    return transaction_id, nonce, exp


def _render_png_data_uri(raw_token: str) -> str:
    """A ``data:`` URI, never a URL under ``storage/qr/``.

    A fetchable URL is a second copy of the credential that outlives the
    response; inlining the bytes keeps the code in exactly one place -- the
    seller's screen.
    """
    code = qrcode.QRCode(
        version=None,
        error_correction=qrcode.ERROR_CORRECT_M,
        box_size=8,
        border=2,
    )
    code.add_data(raw_token)
    code.make(fit=True)

    buffer = io.BytesIO()
    code.make_image(fill_color="black", back_color="white").save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


async def _get_handoff(session: AsyncSession, transaction_id: str) -> QRHandoff | None:
    return (
        await session.execute(
            select(QRHandoff).where(QRHandoff.transaction_id == transaction_id)
        )
    ).scalar_one_or_none()


# ---------------------------------------------------------------------------
# generation
# ---------------------------------------------------------------------------


async def generate_handoff(
    session: AsyncSession, transaction: Transaction, actor: User
) -> tuple[QRHandoff, str, str]:
    """Seller-only. Returns ``(handoff, raw_token, png_data_uri)``.

    The raw token is returned exactly once, here, and never persisted. The
    caller hands it to the seller's screen and forgets it.
    """
    if actor.user_id != transaction.seller_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the seller can generate the handoff code.",
        )
    if transaction.status not in _GENERATABLE:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"A handoff code cannot be generated while this transaction is "
                f"{transaction.status}. Accept the request first."
            ),
        )

    nonce = secrets.token_urlsafe(16)
    expires_at = utcnow() + timedelta(minutes=settings.qr_ttl_minutes)
    payload = f"{transaction.transaction_id}:{nonce}:{_epoch(expires_at)}"
    raw_token = f"{payload}:{sign_payload(payload)}"
    # randbelow, not random.randint: this is a credential, not a dice roll.
    manual_code = f"{secrets.randbelow(1_000_000):06d}"

    handoff = await _get_handoff(session, transaction.transaction_id)
    if handoff is None:
        handoff = QRHandoff(
            transaction_id=transaction.transaction_id,
            token_hash=hash_token(raw_token),
            nonce=nonce,
            manual_code=manual_code,
            expires_at=expires_at,
        )
    else:
        # Regenerating retires the old token and clears the consumed marker,
        # but keeps whichever party has already confirmed -- otherwise a seller
        # refreshing an expired code would silently undo the buyer's scan.
        handoff.token_hash = hash_token(raw_token)
        handoff.nonce = nonce
        handoff.manual_code = manual_code
        handoff.expires_at = expires_at
        handoff.consumed_at = None
    session.add(handoff)
    await session.commit()
    await session.refresh(handoff)

    if transaction.status == TransactionStatus.ACCEPTED:
        # transactions.py owns every write to status, including this one.
        await transaction_service.transition(
            session, transaction, actor, TransactionStatus.AWAITING_HANDOFF
        )

    return handoff, raw_token, _render_png_data_uri(raw_token)


# ---------------------------------------------------------------------------
# verification
# ---------------------------------------------------------------------------


def _assert_unexpired(handoff: QRHandoff) -> None:
    if utcnow() > handoff.expires_at:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=_EXPIRED_DETAIL
        )


def _verify_token(handoff: QRHandoff, transaction: Transaction, token: str) -> None:
    transaction_id, nonce, exp = _decode(token)

    if transaction_id != transaction.transaction_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This code belongs to a different transaction.",
        )
    if _epoch(utcnow()) > exp:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=_EXPIRED_DETAIL
        )
    # A correctly signed but superseded token: the nonce is one-per-code, so a
    # mismatch means the seller has regenerated since this screenshot was taken.
    if not constant_time_equals(nonce, handoff.nonce) or not constant_time_equals(
        hash_token(token), handoff.token_hash
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=_STALE_DETAIL
        )


def _verify_manual_code(handoff: QRHandoff, manual_code: str) -> None:
    if not constant_time_equals(manual_code.strip(), handoff.manual_code):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="That code is not correct. Check the six digits and try again.",
        )


async def verify_handoff(
    session: AsyncSession,
    transaction: Transaction,
    actor: User,
    *,
    token: str | None = None,
    manual_code: str | None = None,
) -> QRHandoff:
    """One call per party; the second one completes the transaction.

    The buyer proves possession of the code (scanned or typed). The seller is
    already holding the phone that generated it, so confirming on their own
    device is proof enough -- asking them to scan their own screen is theatre.
    """
    handoff = await _get_handoff(session, transaction.transaction_id)
    if handoff is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No handoff code has been generated for this transaction yet.",
        )
    if transaction.status != TransactionStatus.AWAITING_HANDOFF:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"This transaction is {transaction.status}; it is not waiting for a "
                "handoff."
            ),
        )
    if handoff.consumed_at is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This code has already been used. Ask the seller to generate a new one.",
        )

    if actor.user_id == transaction.seller_id:
        handoff.verified_by_seller = True
    elif actor.user_id == transaction.buyer_id:
        # Expiry before credentials: "expired" is actionable, "wrong code" is not.
        _assert_unexpired(handoff)
        if token:
            _verify_token(handoff, transaction, token)
        elif manual_code:
            _verify_manual_code(handoff, manual_code)
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Scan the QR code or enter the 6-digit code.",
            )
        handoff.verified_by_buyer = True
    else:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the buyer and seller of this transaction can confirm the handoff.",
        )

    both_confirmed = handoff.verified_by_buyer and handoff.verified_by_seller
    if both_confirmed:
        now = utcnow()
        handoff.verified_at = now
        # Burning the nonce here, not at first scan, is what makes the code
        # single-use: after this point no token or manual code works again.
        handoff.consumed_at = now

    session.add(handoff)
    await session.commit()

    if both_confirmed:
        await transaction_service.complete_transaction(session, transaction)

    await session.refresh(handoff)
    return handoff


__all__ = ["generate_handoff", "verify_handoff"]
