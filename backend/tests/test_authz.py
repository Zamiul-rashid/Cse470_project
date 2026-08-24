"""The authorization matrix.

One table, four kinds of caller, and every route where getting the answer wrong
leaks something: the admin panel, the personal export, somebody else's listing,
the full file behind a preview, and a transaction between two other people.

Two conventions the expectations encode:

* **401 versus 403 is not cosmetic.** No credentials at all is 401 ("sign in");
  credentials that are simply not enough is 403 ("you, specifically, may not").
  A route that answers 403 to an anonymous caller has told them the resource
  exists before asking who they are.
* **``/me/export`` returns 200 to an ordinary student.** That is the test. The
  route takes no user id, so "export someone else's history" is not a request
  anybody can express -- and a case expecting 403 here would be testing a
  restriction that should not exist.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from httpx import AsyncClient

from tests.conftest import Actor, ListingFactory, TransactionFactory, UserFactory


@dataclass(slots=True)
class World:
    """Four accounts and the rows they are allowed -- or not -- to touch."""

    actors: dict[str, Actor]
    ids: dict[str, str] = field(default_factory=dict)


@pytest.fixture
async def world(
    client: AsyncClient,
    campus: dict[str, str],
    make_user: UserFactory,
    make_admin: UserFactory,
    make_listing: ListingFactory,
    make_transaction: TransactionFactory,
) -> World:
    admin = await make_admin(campus)
    owner = await make_user(campus, name="Owner")
    stranger = await make_user(campus, name="Stranger")
    buyer = await make_user(campus, name="Buyer")

    pending_a = await make_listing(owner, title="Queued Notes A")
    pending_b = await make_listing(owner, title="Queued Notes B")
    owned = await make_listing(owner, title="Published Notes", approve_as=admin)
    traded = await make_listing(owner, title="Spoken-For Notes", approve_as=admin)

    transaction = await make_transaction(buyer, traded, owner)

    reported = await client.post(
        "/reports",
        headers=stranger.headers,
        json={"listing_id": traded["listing_id"], "reason": "COPYRIGHT"},
    )
    assert reported.status_code == 201, reported.text

    return World(
        actors={"admin": admin, "owner": owner, "stranger": stranger, "buyer": buyer},
        ids={
            "pending_a": pending_a["listing_id"],
            "pending_b": pending_b["listing_id"],
            "owned": owned["listing_id"],
            "traded": traded["listing_id"],
            "report": reported.json()["report_id"],
            "transaction": transaction["transaction_id"],
        },
    )


#: ``(role, method, path, body, expected_status)``. ``role`` "anonymous" sends no
#: Authorization header at all.
CASES: list[tuple[str, str, str, dict[str, Any] | None, int]] = [
    # --- FR 4.1 moderation queue ---
    ("anonymous", "GET", "/admin/moderation/queue", None, 401),
    ("stranger", "GET", "/admin/moderation/queue", None, 403),
    ("admin", "GET", "/admin/moderation/queue", None, 200),
    ("anonymous", "POST", "/admin/materials/{pending_a}/approve", None, 401),
    ("stranger", "POST", "/admin/materials/{pending_a}/approve", None, 403),
    ("admin", "POST", "/admin/materials/{pending_a}/approve", None, 200),
    ("anonymous", "POST", "/admin/materials/{pending_b}/reject", {"note": "Blurry."}, 401),
    ("stranger", "POST", "/admin/materials/{pending_b}/reject", {"note": "Blurry."}, 403),
    ("admin", "POST", "/admin/materials/{pending_b}/reject", {"note": "Blurry."}, 200),
    # --- FR 3.5 / 4.1 report queue ---
    ("anonymous", "GET", "/admin/reports", None, 401),
    ("stranger", "GET", "/admin/reports", None, 403),
    ("admin", "GET", "/admin/reports", None, 200),
    ("anonymous", "POST", "/admin/reports/{report}/resolve", {"action": "DISMISS"}, 401),
    # The reporter is not a moderator: filing a report buys no say in the outcome.
    ("stranger", "POST", "/admin/reports/{report}/resolve", {"action": "DISMISS"}, 403),
    ("admin", "POST", "/admin/reports/{report}/resolve", {"action": "DISMISS"}, 200),
    ("anonymous", "GET", "/admin/stats", None, 401),
    ("stranger", "GET", "/admin/stats", None, 403),
    ("admin", "GET", "/admin/stats", None, 200),
    # --- FR 4.5 export: own history, and no way to name anyone else's ---
    ("anonymous", "GET", "/me/export?format=csv", None, 401),
    ("stranger", "GET", "/me/export?format=csv", None, 200),
    ("owner", "GET", "/me/export?format=pdf", None, 200),
    # --- somebody else's listing ---
    ("anonymous", "PATCH", "/materials/{owned}", {"title": "Renamed by nobody"}, 401),
    ("stranger", "PATCH", "/materials/{owned}", {"title": "Renamed by a stranger"}, 403),
    ("owner", "PATCH", "/materials/{owned}", {"title": "Renamed by its owner"}, 200),
    ("anonymous", "DELETE", "/materials/{owned}", None, 401),
    ("stranger", "DELETE", "/materials/{owned}", None, 403),
    ("owner", "DELETE", "/materials/{owned}", None, 200),
    # --- the full file behind the preview (FR 1.4) ---
    ("anonymous", "GET", "/materials/{owned}/file", None, 401),
    ("stranger", "GET", "/materials/{owned}/file", None, 403),
    ("buyer", "GET", "/materials/{owned}/file", None, 403),
    ("owner", "GET", "/materials/{owned}/file", None, 200),
    # An admin has to be able to open what a report is about.
    ("admin", "GET", "/materials/{owned}/file", None, 200),
    # --- a transaction between two other people ---
    ("anonymous", "GET", "/transactions/{transaction}", None, 401),
    ("stranger", "GET", "/transactions/{transaction}", None, 403),
    ("buyer", "GET", "/transactions/{transaction}", None, 200),
    ("owner", "GET", "/transactions/{transaction}", None, 200),
    ("admin", "GET", "/transactions/{transaction}", None, 200),
]


@pytest.mark.parametrize(
    ("role", "method", "path", "body", "expected"),
    CASES,
    ids=[f"{role}-{method}-{path}" for role, method, path, _body, _expected in CASES],
)
async def test_authorization_matrix(
    client: AsyncClient,
    world: World,
    role: str,
    method: str,
    path: str,
    body: dict[str, Any] | None,
    expected: int,
) -> None:
    headers = {} if role == "anonymous" else world.actors[role].headers

    response = await client.request(
        method, path.format(**world.ids), headers=headers, json=body
    )

    assert response.status_code == expected, (
        f"{role} {method} {path} -> {response.status_code}: {response.text}"
    )


async def test_a_hidden_listing_is_a_404_not_a_403(
    client: AsyncClient, world: World
) -> None:
    """A PENDING listing is between its uploader and the moderators.

    403 would confirm it exists, which is itself information the caller has no
    claim to -- so the visibility rule answers 404 and the ownership rule (which
    only applies to listings you can already see) answers 403.
    """
    stranger = world.actors["stranger"]

    hidden = await client.get(
        f"/materials/{world.ids['pending_a']}", headers=stranger.headers
    )
    assert hidden.status_code == 404

    visible = await client.get(
        f"/materials/{world.ids['owned']}", headers=stranger.headers
    )
    assert visible.status_code == 200


async def test_the_status_filter_cannot_be_pointed_at_the_moderation_queue(
    client: AsyncClient, world: World
) -> None:
    """``GET /materials?status=PENDING`` is the one search parameter that could
    otherwise expose unmoderated uploads to anybody who guessed at it."""
    stranger = world.actors["stranger"]
    owner = world.actors["owner"]

    refused = await client.get(
        "/materials", headers=stranger.headers, params={"status": "PENDING"}
    )
    assert refused.status_code == 403

    borrowed = await client.get(
        "/materials",
        headers=stranger.headers,
        params={"status": "PENDING", "uploader_id": owner.user_id},
    )
    assert borrowed.status_code == 403

    own = await client.get(
        "/materials",
        headers=owner.headers,
        params={"status": "PENDING", "uploader_id": owner.user_id},
    )
    assert own.status_code == 200
    assert own.json()["total"] == 2
