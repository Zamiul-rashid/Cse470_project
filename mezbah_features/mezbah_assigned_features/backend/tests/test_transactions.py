"""The transaction state machine and the review gate (FR 3.4, FR 2.5).

An illegal transition returning 500 is the single most common source of
demo-day crashes, so the first test here is the whole
matrix: every ``(source, target)`` pair the vocabulary allows to be *expressed*
and ``TRANSACTION_TRANSITIONS`` refuses to *perform*, driven through the real
endpoint from a real transaction actually sitting in that state.

``COMPLETED`` is in that list from every source, including the one the matrix
permits. Completion is not a transition: it is reachable only through the QR
handoff both parties confirm, and a PATCH that could reach it would hand either
party a button to close a meeting that never happened.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import select

from app.models import TRANSACTION_TRANSITIONS, TransactionStatus, User
from tests.conftest import Actor, ListingFactory, TransactionFactory, UserFactory

STATUSES: tuple[TransactionStatus, ...] = tuple(TRANSACTION_TRANSITIONS)

#: Every move the state machine must refuse. ``COMPLETED`` is always here --
#: see the module docstring.
ILLEGAL_MOVES: list[tuple[TransactionStatus, TransactionStatus]] = [
    (source, target)
    for source in STATUSES
    for target in STATUSES
    if target not in TRANSACTION_TRANSITIONS[source]
    or target == TransactionStatus.COMPLETED
]


@dataclass(slots=True)
class Deal:
    """One approved listing and the two people on either side of it."""

    seller: Actor
    buyer: Actor
    listing: dict[str, Any]


@pytest.fixture
async def deal(
    campus: dict[str, str],
    admin: Actor,
    make_user: UserFactory,
    make_listing: ListingFactory,
) -> Deal:
    seller = await make_user(campus, name="Seller")
    buyer = await make_user(campus, name="Buyer")
    listing = await make_listing(
        seller, title="Database Systems Notes", price=400, approve_as=admin
    )
    return Deal(seller=seller, buyer=buyer, listing=listing)


@pytest.mark.parametrize(
    ("source", "target"),
    ILLEGAL_MOVES,
    ids=[f"{source.value}->{target.value}" for source, target in ILLEGAL_MOVES],
)
async def test_every_illegal_transition_is_a_conflict(
    client: AsyncClient,
    deal: Deal,
    make_transaction: TransactionFactory,
    source: TransactionStatus,
    target: TransactionStatus,
) -> None:
    transaction = await make_transaction(
        deal.buyer, deal.listing, deal.seller, status=source
    )
    transaction_id = transaction["transaction_id"]
    assert transaction["status"] == source.value

    response = await client.patch(
        f"/transactions/{transaction_id}/status",
        # The seller is a party to every one of these, so a 403 can never stand
        # in for the 409 under test.
        headers=deal.seller.headers,
        json={"status": target.value},
    )

    assert response.status_code == 409, response.text
    assert response.json()["detail"]

    unchanged = await client.get(
        f"/transactions/{transaction_id}", headers=deal.seller.headers
    )
    assert unchanged.json()["status"] == source.value


async def test_only_the_seller_can_accept(
    client: AsyncClient, deal: Deal, make_transaction: TransactionFactory
) -> None:
    transaction = await make_transaction(deal.buyer, deal.listing, deal.seller)

    refused = await client.patch(
        f"/transactions/{transaction['transaction_id']}/status",
        headers=deal.buyer.headers,
        json={"status": TransactionStatus.ACCEPTED.value},
    )
    assert refused.status_code == 403

    accepted = await client.patch(
        f"/transactions/{transaction['transaction_id']}/status",
        headers=deal.seller.headers,
        json={"status": TransactionStatus.ACCEPTED.value},
    )
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "ACCEPTED"


async def test_requesting_reserves_the_listing_and_cancelling_releases_it(
    client: AsyncClient, deal: Deal, make_transaction: TransactionFactory
) -> None:
    transaction = await make_transaction(deal.buyer, deal.listing, deal.seller)

    reserved = await client.get(
        f"/materials/{deal.listing['listing_id']}", headers=deal.buyer.headers
    )
    assert reserved.json()["status"] == "RESERVED"

    # RESERVED is what stops a competing transaction being opened on it.
    second = await client.post(
        "/transactions",
        headers=deal.buyer.headers,
        json={"listing_id": deal.listing["listing_id"]},
    )
    assert second.status_code == 409

    cancelled = await client.patch(
        f"/transactions/{transaction['transaction_id']}/status",
        headers=deal.buyer.headers,
        json={"status": TransactionStatus.CANCELLED.value},
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "CANCELLED"

    released = await client.get(
        f"/materials/{deal.listing['listing_id']}", headers=deal.buyer.headers
    )
    assert released.json()["status"] == "APPROVED"


async def test_a_seller_cannot_buy_their_own_listing(
    client: AsyncClient, deal: Deal
) -> None:
    response = await client.post(
        "/transactions",
        headers=deal.seller.headers,
        json={"listing_id": deal.listing["listing_id"]},
    )
    assert response.status_code == 403


async def test_a_review_before_completion_is_refused(
    client: AsyncClient, deal: Deal, make_transaction: TransactionFactory
) -> None:
    transaction = await make_transaction(
        deal.buyer, deal.listing, deal.seller, status=TransactionStatus.ACCEPTED
    )
    assert transaction["can_review"] is False

    response = await client.post(
        "/reviews",
        headers=deal.buyer.headers,
        json={"transaction_id": transaction["transaction_id"], "rating": 5},
    )
    assert response.status_code == 409
    assert "ACCEPTED" in response.json()["detail"]


async def test_a_second_review_of_the_same_transaction_is_refused(
    client: AsyncClient, deal: Deal, make_transaction: TransactionFactory
) -> None:
    transaction = await make_transaction(
        deal.buyer, deal.listing, deal.seller, status=TransactionStatus.COMPLETED
    )

    first = await client.post(
        "/reviews",
        headers=deal.buyer.headers,
        json={
            "transaction_id": transaction["transaction_id"],
            "rating": 5,
            "comment": "Met on time, book as described.",
        },
    )
    assert first.status_code == 201, first.text
    # The reviewee is derived from the transaction, never sent by the client.
    assert first.json()["reviewee_id"] == deal.seller.user_id
    assert first.json()["reviewer_id"] == deal.buyer.user_id

    second = await client.post(
        "/reviews",
        headers=deal.buyer.headers,
        json={"transaction_id": transaction["transaction_id"], "rating": 1},
    )
    assert second.status_code == 409
    assert "already reviewed" in second.json()["detail"]


async def test_a_stranger_cannot_review_a_transaction(
    client: AsyncClient,
    deal: Deal,
    campus: dict[str, str],
    make_user: UserFactory,
    make_transaction: TransactionFactory,
) -> None:
    outsider = await make_user(campus, name="Passer By")
    transaction = await make_transaction(
        deal.buyer, deal.listing, deal.seller, status=TransactionStatus.COMPLETED
    )

    response = await client.post(
        "/reviews",
        headers=outsider.headers,
        json={"transaction_id": transaction["transaction_id"], "rating": 1},
    )
    assert response.status_code == 403


async def test_rating_average_is_recomputed_from_the_reviews(
    client: AsyncClient,
    campus: dict[str, str],
    admin: Actor,
    make_user: UserFactory,
    make_listing: ListingFactory,
    make_transaction: TransactionFactory,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    seller = await make_user(campus, name="Seller")
    buyers = [
        await make_user(campus, name="First Buyer"),
        await make_user(campus, name="Second Buyer"),
    ]

    for buyer, rating in zip(buyers, (5, 4), strict=True):
        listing = await make_listing(seller, price=300, approve_as=admin)
        transaction = await make_transaction(
            buyer, listing, seller, status=TransactionStatus.COMPLETED
        )
        left = await client.post(
            "/reviews",
            headers=buyer.headers,
            json={"transaction_id": transaction["transaction_id"], "rating": rating},
        )
        assert left.status_code == 201, left.text

    profile = await client.get(f"/users/{seller.user_id}", headers=admin.headers)
    assert profile.status_code == 200
    assert profile.json()["rating_avg"] == 4.5
    assert profile.json()["rating_count"] == 2

    # The profile aggregates the reviews table directly, so it would look right
    # even if the cached columns were never written. Those columns are what the
    # leaderboard and the listing card sort on, so read them too.
    async with session_factory() as open_session:
        stored = (
            await open_session.execute(
                select(User).where(User.user_id == seller.user_id)
            )
        ).scalar_one()
    assert stored.rating_avg == 4.5
    assert stored.rating_count == 2


async def test_history_lists_both_sides_of_the_deal(
    client: AsyncClient, deal: Deal, make_transaction: TransactionFactory
) -> None:
    transaction = await make_transaction(deal.buyer, deal.listing, deal.seller)

    as_buyer = await client.get("/transactions/me", headers=deal.buyer.headers)
    as_seller = await client.get("/transactions/me", headers=deal.seller.headers)

    assert [item["transaction_id"] for item in as_buyer.json()["items"]] == [
        transaction["transaction_id"]
    ]
    assert as_buyer.json()["items"][0]["role"] == "buyer"
    assert as_seller.json()["items"][0]["role"] == "seller"
    assert as_seller.json()["items"][0]["listing_title"] == deal.listing["title"]
