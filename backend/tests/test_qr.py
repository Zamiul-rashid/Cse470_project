"""QR handoff verification (FR 3.3).

A handoff code that carries a bare transaction id fails the requirement twice
over: a screenshot works forever, and one party can confirm a meeting that never
happened. Every test below is one of those two failures, written as an attack:

* replay a token after it has been consumed;
* replay a token the seller has since regenerated past;
* present a token whose row has expired;
* confirm from an account that is not on the transaction;
* confirm both halves from the seller's own device.

The manual-code branch gets the same happy path as the scanner, because on a
demo machine outside a secure context it is the *only* branch that runs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.util import utcnow
from app.models import QRHandoff
from tests.conftest import Actor, ListingFactory, TransactionFactory, UserFactory


@dataclass(slots=True)
class Handoff:
    """A transaction sitting at ACCEPTED with both parties' tokens to hand."""

    seller: Actor
    buyer: Actor
    listing: dict[str, Any]
    transaction: dict[str, Any]

    @property
    def transaction_id(self) -> str:
        return self.transaction["transaction_id"]


@pytest.fixture
async def handoff(
    campus: dict[str, str],
    admin: Actor,
    make_user: UserFactory,
    make_listing: ListingFactory,
    make_transaction: TransactionFactory,
) -> Handoff:
    seller = await make_user(campus, name="Seller")
    buyer = await make_user(campus, name="Buyer")
    listing = await make_listing(
        seller, title="Compilers Lecture Notes", price=650, approve_as=admin
    )
    transaction = await make_transaction(
        buyer, listing, seller, status="ACCEPTED"
    )
    return Handoff(seller=seller, buyer=buyer, listing=listing, transaction=transaction)


async def _generate(client: AsyncClient, handoff: Handoff) -> dict[str, Any]:
    response = await client.post(
        f"/transactions/{handoff.transaction_id}/qr", headers=handoff.seller.headers
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_both_parties_confirming_completes_the_transaction(
    client: AsyncClient, handoff: Handoff
) -> None:
    code = await _generate(client, handoff)

    assert code["qr_png_data_uri"].startswith("data:image/png;base64,")
    assert code["manual_code"].isdigit() and len(code["manual_code"]) == 6
    assert code["token"].count(":") == 3

    # Generating from ACCEPTED moves the deal on by itself -- the seller is
    # holding the code out, which *is* the handoff starting.
    opened = await client.get(
        f"/transactions/{handoff.transaction_id}", headers=handoff.buyer.headers
    )
    assert opened.json()["status"] == "AWAITING_HANDOFF"
    assert opened.json()["handoff"]["generated"] is True

    scanned = await client.post(
        f"/transactions/{handoff.transaction_id}/qr/verify",
        headers=handoff.buyer.headers,
        json={"token": code["token"]},
    )
    assert scanned.status_code == 200, scanned.text
    assert scanned.json()["status"] == "AWAITING_HANDOFF"
    assert scanned.json()["handoff"]["verified_by_buyer"] is True
    assert scanned.json()["handoff"]["verified_by_seller"] is False

    confirmed = await client.post(
        f"/transactions/{handoff.transaction_id}/qr/verify",
        headers=handoff.seller.headers,
        json={},
    )
    assert confirmed.status_code == 200, confirmed.text
    body = confirmed.json()
    assert body["status"] == "COMPLETED"
    assert body["completed_at"] is not None
    assert body["handoff"]["verified_at"] is not None
    # The review gate opens here and nowhere else (FR 2.5).
    assert body["can_review"] is True

    listing = await client.get(
        f"/materials/{handoff.listing['listing_id']}", headers=handoff.seller.headers
    )
    assert listing.json()["status"] == "COMPLETED"

    # The buyer's download unlocks with the handoff, not with the request.
    file_response = await client.get(
        f"/materials/{handoff.listing['listing_id']}/file", headers=handoff.buyer.headers
    )
    assert file_response.status_code == 200


async def test_the_manual_code_completes_the_handoff_too(
    client: AsyncClient, handoff: Handoff
) -> None:
    code = await _generate(client, handoff)

    typed = await client.post(
        f"/transactions/{handoff.transaction_id}/qr/verify",
        headers=handoff.buyer.headers,
        json={"manual_code": code["manual_code"]},
    )
    assert typed.status_code == 200, typed.text
    assert typed.json()["handoff"]["verified_by_buyer"] is True

    confirmed = await client.post(
        f"/transactions/{handoff.transaction_id}/qr/verify",
        headers=handoff.seller.headers,
        json={},
    )
    assert confirmed.json()["status"] == "COMPLETED"


async def test_a_wrong_manual_code_is_refused(
    client: AsyncClient, handoff: Handoff
) -> None:
    code = await _generate(client, handoff)
    wrong = "000000" if code["manual_code"] != "000000" else "111111"

    response = await client.post(
        f"/transactions/{handoff.transaction_id}/qr/verify",
        headers=handoff.buyer.headers,
        json={"manual_code": wrong},
    )
    assert response.status_code == 400
    assert "six digits" in response.json()["detail"]


async def test_a_consumed_token_cannot_be_replayed(
    client: AsyncClient, handoff: Handoff
) -> None:
    code = await _generate(client, handoff)

    for headers, body in (
        (handoff.buyer.headers, {"token": code["token"]}),
        (handoff.seller.headers, {}),
    ):
        assert (
            await client.post(
                f"/transactions/{handoff.transaction_id}/qr/verify",
                headers=headers,
                json=body,
            )
        ).status_code == 200

    # The screenshot still exists and the signature still checks out. The
    # transaction is done, so the code is not.
    replayed = await client.post(
        f"/transactions/{handoff.transaction_id}/qr/verify",
        headers=handoff.buyer.headers,
        json={"token": code["token"]},
    )
    assert replayed.status_code == 409
    assert "COMPLETED" in replayed.json()["detail"]


async def test_a_superseded_token_is_refused(
    client: AsyncClient, handoff: Handoff
) -> None:
    stale = await _generate(client, handoff)
    fresh = await _generate(client, handoff)
    assert stale["token"] != fresh["token"]

    replayed = await client.post(
        f"/transactions/{handoff.transaction_id}/qr/verify",
        headers=handoff.buyer.headers,
        json={"token": stale["token"]},
    )
    assert replayed.status_code == 400
    assert "no longer valid" in replayed.json()["detail"]

    # Regenerating retires the old code without undoing anybody's confirmation.
    assert (
        await client.post(
            f"/transactions/{handoff.transaction_id}/qr/verify",
            headers=handoff.buyer.headers,
            json={"token": fresh["token"]},
        )
    ).status_code == 200


async def test_an_expired_code_is_refused(
    client: AsyncClient, handoff: Handoff, session: AsyncSession
) -> None:
    code = await _generate(client, handoff)

    # No endpoint can produce this state -- the TTL is ten minutes and nothing
    # accepts an expiry from the client -- so the row is aged by hand.
    row = (
        await session.execute(
            select(QRHandoff).where(
                QRHandoff.transaction_id == handoff.transaction_id
            )
        )
    ).scalar_one()
    row.expires_at = utcnow() - timedelta(minutes=1)
    session.add(row)
    await session.commit()

    response = await client.post(
        f"/transactions/{handoff.transaction_id}/qr/verify",
        headers=handoff.buyer.headers,
        json={"token": code["token"]},
    )
    assert response.status_code == 400
    assert "expired" in response.json()["detail"]

    still_open = await client.get(
        f"/transactions/{handoff.transaction_id}", headers=handoff.buyer.headers
    )
    assert still_open.json()["status"] == "AWAITING_HANDOFF"


async def test_a_third_party_can_neither_generate_nor_verify(
    client: AsyncClient,
    handoff: Handoff,
    campus: dict[str, str],
    make_user: UserFactory,
) -> None:
    outsider = await make_user(campus, name="Passer By")
    code = await _generate(client, handoff)

    verify = await client.post(
        f"/transactions/{handoff.transaction_id}/qr/verify",
        headers=outsider.headers,
        json={"token": code["token"]},
    )
    assert verify.status_code == 403
    assert verify.json()["detail"] == "This transaction is not yours."

    generate = await client.post(
        f"/transactions/{handoff.transaction_id}/qr", headers=outsider.headers
    )
    assert generate.status_code == 403


async def test_the_seller_cannot_confirm_both_halves(
    client: AsyncClient, handoff: Handoff
) -> None:
    await _generate(client, handoff)

    for _ in range(2):
        response = await client.post(
            f"/transactions/{handoff.transaction_id}/qr/verify",
            headers=handoff.seller.headers,
            json={},
        )
        assert response.status_code == 200

    # Two taps on the seller's own device are still one party.
    assert response.json()["status"] == "AWAITING_HANDOFF"
    assert response.json()["handoff"]["verified_by_buyer"] is False


async def test_the_buyer_cannot_confirm_with_an_empty_body(
    client: AsyncClient, handoff: Handoff
) -> None:
    await _generate(client, handoff)

    response = await client.post(
        f"/transactions/{handoff.transaction_id}/qr/verify",
        headers=handoff.buyer.headers,
        json={},
    )
    assert response.status_code == 400
    assert "Scan the QR code" in response.json()["detail"]


async def test_a_code_cannot_be_raised_before_the_request_is_accepted(
    client: AsyncClient,
    campus: dict[str, str],
    admin: Actor,
    make_user: UserFactory,
    make_listing: ListingFactory,
    make_transaction: TransactionFactory,
) -> None:
    seller = await make_user(campus)
    buyer = await make_user(campus)
    listing = await make_listing(seller, approve_as=admin)
    transaction = await make_transaction(buyer, listing, seller)

    response = await client.post(
        f"/transactions/{transaction['transaction_id']}/qr", headers=seller.headers
    )
    assert response.status_code == 409
    assert "REQUESTED" in response.json()["detail"]
