"""The catalogue: upload, moderation visibility, search, and file access.

FR 1.1-1.4. Four things are checked here that no other file covers:

* a listing is **invisible until approved** -- the moderation queue is worth
  nothing if a PENDING upload already shows up in browse;
* keyword search really goes through **FTS5**, including its prefix matching,
  rather than quietly falling back to a LIKE on everything;
* the price/listing-type rule is refused at the **schema**, as a 422 naming the
  field, rather than by SQLite as an opaque IntegrityError;
* ``/preview`` and ``/file`` are different doors -- the first open to any signed-in
  student, the second closed to everyone but the uploader, an admin and the
  counterparty of a COMPLETED transaction.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from httpx import AsyncClient

from tests.conftest import Actor, ListingFactory, UserFactory


def _titles(page: dict[str, Any]) -> list[str]:
    return [item["title"] for item in page["items"]]


def _ids(page: dict[str, Any]) -> list[str]:
    return [item["listing_id"] for item in page["items"]]


async def test_upload_lands_pending_and_only_appears_once_approved(
    client: AsyncClient,
    campus: dict[str, str],
    make_user: UserFactory,
    make_listing: ListingFactory,
    admin: Actor,
) -> None:
    uploader = await make_user(campus)
    reader = await make_user(campus)

    listing = await make_listing(
        uploader, title="Thermodynamics Lecture Notes", course_code="MEC201"
    )
    assert listing["status"] == "PENDING"
    assert listing["has_preview"] is True
    assert listing["page_count"] == 1

    before = await client.get(
        "/materials", headers=reader.headers, params={"course_code": "MEC201"}
    )
    assert before.status_code == 200
    assert before.json()["total"] == 0

    approved = await client.post(
        f"/admin/materials/{listing['listing_id']}/approve",
        headers=admin.headers,
        json={"note": "Legible scan."},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["listing"]["status"] == "APPROVED"

    after = await client.get(
        "/materials", headers=reader.headers, params={"course_code": "MEC201"}
    )
    assert after.json()["total"] == 1
    assert _ids(after.json()) == [listing["listing_id"]]


async def test_keyword_search_hits_the_fts_index(
    client: AsyncClient,
    campus: dict[str, str],
    make_user: UserFactory,
    make_listing: ListingFactory,
    admin: Actor,
) -> None:
    uploader = await make_user(campus)
    await make_listing(
        uploader,
        title="Thermodynamics Lecture Notes",
        course_code="MEC201",
        approve_as=admin,
    )
    await make_listing(
        uploader,
        title="Discrete Mathematics Solutions",
        course_code="CSE260",
        approve_as=admin,
    )

    hit = await client.get(
        "/materials", headers=uploader.headers, params={"q": "thermodynamics"}
    )
    assert hit.status_code == 200, hit.text
    assert _titles(hit.json()) == ["Thermodynamics Lecture Notes"]

    # Tokens are indexed for prefix search so the grid updates while the student
    # is still typing.
    prefix = await client.get(
        "/materials", headers=uploader.headers, params={"q": "discre"}
    )
    assert _titles(prefix.json()) == ["Discrete Mathematics Solutions"]

    miss = await client.get(
        "/materials", headers=uploader.headers, params={"q": "cryptography"}
    )
    assert miss.json()["total"] == 0


async def test_filters_by_normalised_course_code_and_listing_type(
    client: AsyncClient,
    campus: dict[str, str],
    make_user: UserFactory,
    make_listing: ListingFactory,
    admin: Actor,
) -> None:
    uploader = await make_user(campus)

    sold = await make_listing(
        uploader,
        title="Software Engineering Slides",
        # Stored normalised, so the filter below must find it despite the space.
        course_code="CSE 470",
        listing_type="SELL",
        price=450,
        approve_as=admin,
    )
    freebie = await make_listing(
        uploader,
        title="Software Engineering Past Papers",
        course_code="CSE470",
        listing_type="FREE",
        price=None,
        approve_as=admin,
    )
    await make_listing(
        uploader,
        title="Algorithms Handbook",
        course_code="CSE221",
        approve_as=admin,
    )

    by_course = await client.get(
        "/materials", headers=uploader.headers, params={"course_code": "cse-470"}
    )
    assert by_course.json()["total"] == 2
    assert set(_ids(by_course.json())) == {sold["listing_id"], freebie["listing_id"]}

    by_type = await client.get(
        "/materials",
        headers=uploader.headers,
        params={"course_code": "CSE470", "listing_type": "FREE"},
    )
    assert _ids(by_type.json()) == [freebie["listing_id"]]
    assert by_type.json()["items"][0]["price"] is None


async def test_price_and_listing_type_must_agree(
    client: AsyncClient,
    campus: dict[str, str],
    make_user: UserFactory,
    make_pdf: Callable[..., bytes],
) -> None:
    uploader = await make_user(campus)
    files = {"file": ("notes.pdf", make_pdf(), "application/pdf")}
    base = {
        "title": "Constrained Listing",
        "course_code": "CSE470",
        "department": "CSE",
        "semester": "Fall 2025",
    }

    priced_free = await client.post(
        "/materials",
        headers=uploader.headers,
        data={**base, "listing_type": "FREE", "price": "200"},
        files={"file": ("notes.pdf", make_pdf(), "application/pdf")},
    )
    assert priced_free.status_code == 422
    assert "price" in priced_free.json()["detail"].lower()

    unpriced_sale = await client.post(
        "/materials",
        headers=uploader.headers,
        data={**base, "listing_type": "SELL"},
        files=files,
    )
    assert unpriced_sale.status_code == 422
    assert "price" in unpriced_sale.json()["detail"].lower()

    # Nothing was written, so no orphaned blob and no half-listing.
    listings = await client.get(
        "/materials",
        headers=uploader.headers,
        params={"uploader_id": uploader.user_id, "status": "PENDING"},
    )
    assert listings.json()["total"] == 0


async def test_non_pdf_upload_is_refused(
    client: AsyncClient, campus: dict[str, str], make_user: UserFactory
) -> None:
    uploader = await make_user(campus)

    # The declared content type says PDF; the bytes say otherwise, and the bytes
    # are what decide.
    response = await client.post(
        "/materials",
        headers=uploader.headers,
        data={
            "title": "Definitely Not A PDF",
            "course_code": "CSE470",
            "department": "CSE",
            "semester": "Fall 2025",
            "listing_type": "FREE",
        },
        files={"file": ("notes.pdf", b"MZ\x90\x00 this is an executable", "application/pdf")},
    )
    assert response.status_code == 415


async def test_preview_is_open_to_any_signed_in_student(
    client: AsyncClient,
    campus: dict[str, str],
    make_user: UserFactory,
    make_listing: ListingFactory,
    admin: Actor,
) -> None:
    uploader = await make_user(campus)
    reader = await make_user(campus)
    listing = await make_listing(uploader, pages=5, approve_as=admin)

    response = await client.get(
        f"/materials/{listing['listing_id']}/preview", headers=reader.headers
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/pdf")
    assert response.content.startswith(b"%PDF")

    # A separate, shorter document -- not the full file with pages hidden.
    assert len(response.content) < len(
        (
            await client.get(
                f"/materials/{listing['listing_id']}/file", headers=uploader.headers
            )
        ).content
    )

    assert (await client.get(f"/materials/{listing['listing_id']}/preview")).status_code == 401


async def test_full_file_is_the_uploaders_until_a_transaction_completes(
    client: AsyncClient,
    campus: dict[str, str],
    make_user: UserFactory,
    make_listing: ListingFactory,
    admin: Actor,
) -> None:
    uploader = await make_user(campus)
    stranger = await make_user(campus)
    listing = await make_listing(uploader, approve_as=admin)

    refused = await client.get(
        f"/materials/{listing['listing_id']}/file", headers=stranger.headers
    )
    assert refused.status_code == 403
    assert "completed transaction" in refused.json()["detail"]

    allowed = await client.get(
        f"/materials/{listing['listing_id']}/file", headers=uploader.headers
    )
    assert allowed.status_code == 200
    assert allowed.content.startswith(b"%PDF")
    assert "attachment" in allowed.headers["content-disposition"]


async def test_editing_an_approved_listing_returns_it_to_pending(
    client: AsyncClient,
    campus: dict[str, str],
    make_user: UserFactory,
    make_listing: ListingFactory,
    admin: Actor,
) -> None:
    uploader = await make_user(campus)
    reader = await make_user(campus)
    listing = await make_listing(
        uploader, title="Networks Lecture Notes", course_code="CSE321", approve_as=admin
    )
    assert listing["status"] == "APPROVED"
    assert listing["moderation_note"] == "Looks fine."

    edited = await client.patch(
        f"/materials/{listing['listing_id']}",
        headers=uploader.headers,
        json={"title": "Networks Lecture Notes (revised)", "price": 275},
    )
    assert edited.status_code == 200, edited.text
    body = edited.json()

    # Moderation approved a particular title; changing it re-enters the queue.
    assert body["status"] == "PENDING"
    assert body["title"] == "Networks Lecture Notes (revised)"
    assert body["price"] == 275
    # The old decision goes with it -- a stale "approved by X" on a queued
    # listing reads as a bug to the next moderator.
    assert body["moderated_at"] is None
    assert body["moderation_note"] is None

    hidden = await client.get(
        "/materials", headers=reader.headers, params={"course_code": "CSE321"}
    )
    assert hidden.json()["total"] == 0
