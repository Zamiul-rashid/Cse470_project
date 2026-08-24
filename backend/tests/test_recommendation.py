"""The RecommendationEngine: duplicate warnings (FR 1.5) and price suggestion (FR 3.1).

Both halves fail in the same direction if they are written naively -- they get
*confident*. So the tests here are about the edges rather than the happy path:

* the similarity threshold is **inclusive**, and a title below it produces no
  candidate at all rather than a weak one;
* an identical ``file_hash`` is a candidate wherever it sits, including under a
  different course code, because the same file uploaded twice is the same file;
* a sample of two returns ``insufficient_data`` and *still* reports its
  ``sample_size`` -- "not enough similar listings yet" is the useful answer, and
  the number behind it is what lets the UI say so.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import Any

import pytest
from httpx import AsyncClient

from app.core.config import settings
from tests.conftest import Actor, ListingFactory, UserFactory


async def _check(
    client: AsyncClient,
    caller: Actor,
    *,
    title: str,
    course_code: str,
    edition: str | None = None,
    file_hash: str | None = None,
) -> list[dict[str, Any]]:
    body: dict[str, Any] = {"title": title, "course_code": course_code}
    if edition is not None:
        body["edition"] = edition
    if file_hash is not None:
        body["file_hash"] = file_hash

    response = await client.post(
        "/materials/check-duplicate", headers=caller.headers, json=body
    )
    assert response.status_code == 200, response.text
    return response.json()["candidates"]


# ---------------------------------------------------------------------------
# FR 1.5 -- duplicate detection
# ---------------------------------------------------------------------------


async def test_identical_file_hash_is_the_strongest_warning(
    client: AsyncClient,
    campus: dict[str, str],
    make_user: UserFactory,
    make_listing: ListingFactory,
    make_pdf: Callable[..., bytes],
) -> None:
    uploader = await make_user(campus)
    content = make_pdf(text="Operating systems, chapter 3")
    listing = await make_listing(
        uploader,
        title="Operating Systems Lecture Notes",
        course_code="CSE321",
        content=content,
    )

    candidates = await _check(
        client,
        uploader,
        # Nothing else about this submission matches: a different title under a
        # different course still has to surface, because the bytes are the same.
        title="Something Entirely Unrelated",
        course_code="EEE101",
        file_hash=hashlib.sha256(content).hexdigest(),
    )

    assert len(candidates) == 1
    assert candidates[0]["listing_id"] == listing["listing_id"]
    assert candidates[0]["similarity"] == 1.0
    assert candidates[0]["reason"] == "identical file"
    assert candidates[0]["uploader_name"] == uploader.name


async def test_same_course_and_edition_with_a_near_title_is_a_candidate(
    client: AsyncClient,
    campus: dict[str, str],
    make_user: UserFactory,
    make_listing: ListingFactory,
) -> None:
    uploader = await make_user(campus)
    listing = await make_listing(
        uploader,
        title="Operating Systems Lecture Notes",
        course_code="CSE321",
        edition="3rd Edition",
    )

    candidates = await _check(
        client,
        uploader,
        title="Lecture Notes, Operating Systems",
        # Course code and edition are both normalised on each side, so case and
        # punctuation are noise -- the words themselves still have to match.
        course_code="cse 321",
        edition="3RD-EDITION",
    )

    assert [candidate["listing_id"] for candidate in candidates] == [
        listing["listing_id"]
    ]
    assert candidates[0]["reason"] == "same course, edition and a near-identical title"
    assert candidates[0]["similarity"] >= settings.duplicate_title_threshold / 100


async def test_an_unrelated_title_produces_no_candidate(
    client: AsyncClient,
    campus: dict[str, str],
    make_user: UserFactory,
    make_listing: ListingFactory,
) -> None:
    uploader = await make_user(campus)
    await make_listing(
        uploader, title="Operating Systems Lecture Notes", course_code="CSE321"
    )

    # Same course, and that is the whole of the resemblance.
    candidates = await _check(
        client,
        uploader,
        title="Midterm Question Bank With Marking Scheme",
        course_code="CSE321",
    )
    assert candidates == []


async def test_the_similarity_threshold_decides_the_boundary(
    client: AsyncClient,
    campus: dict[str, str],
    make_user: UserFactory,
    make_listing: ListingFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One title pair, walked across the threshold.

    "Slides" against "Notes" scores in the high eighties -- comfortably above the
    default 85 and below 100. Moving the setting rather than the titles is what
    makes this a test of the boundary itself instead of a test of rapidfuzz's
    exact arithmetic.
    """
    uploader = await make_user(campus)
    await make_listing(
        uploader, title="Operating Systems Lecture Notes", course_code="CSE321"
    )

    near_title = "Operating Systems Lecture Slides"
    assert len(await _check(client, uploader, title=near_title, course_code="CSE321")) == 1

    monkeypatch.setattr(settings, "duplicate_title_threshold", 100)
    assert await _check(client, uploader, title=near_title, course_code="CSE321") == []

    # The comparison is ``score < threshold``, so the threshold value itself is
    # still a match -- 100 does not mean "unreachable".
    exact = await _check(
        client, uploader, title="Operating Systems Lecture Notes", course_code="CSE321"
    )
    assert len(exact) == 1
    assert exact[0]["similarity"] == 1.0


# ---------------------------------------------------------------------------
# FR 3.1 -- price suggestion
# ---------------------------------------------------------------------------


async def _suggest(
    client: AsyncClient, caller: Actor, **params: Any
) -> dict[str, Any]:
    response = await client.get(
        "/materials/price-suggestion", headers=caller.headers, params=params
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_a_thin_sample_admits_it(
    client: AsyncClient,
    campus: dict[str, str],
    make_user: UserFactory,
    make_listing: ListingFactory,
    admin: Actor,
) -> None:
    seller = await make_user(campus)
    for price in (400, 600):
        await make_listing(
            seller,
            course_code="CSE470",
            edition="1st Edition",
            listing_type="SELL",
            price=price,
            approve_as=admin,
        )

    suggestion = await _suggest(
        client, seller, course_code="CSE470", edition="1st Edition"
    )

    assert suggestion["basis"] == "insufficient_data"
    assert suggestion["suggested"] is None
    assert suggestion["low"] is None
    assert suggestion["high"] is None
    # Reported anyway: it is what lets the UI say *why* there is no number.
    assert suggestion["sample_size"] == 2
    assert settings.price_min_sample == 3


async def test_three_comparable_listings_produce_a_median_and_a_range(
    client: AsyncClient,
    campus: dict[str, str],
    make_user: UserFactory,
    make_listing: ListingFactory,
    admin: Actor,
) -> None:
    seller = await make_user(campus)
    for price in (100, 200, 300):
        await make_listing(
            seller,
            course_code="CSE470",
            edition="1st Edition",
            listing_type="SELL",
            price=price,
            approve_as=admin,
        )

    suggestion = await _suggest(
        client,
        seller,
        # Normalised, like every other course-code lookup in the system.
        course_code="cse 470",
        edition="1ST EDITION",
        listing_type="SELL",
    )

    assert suggestion["basis"] == "course_edition"
    assert suggestion["sample_size"] == 3
    assert suggestion["suggested"] == 200.0
    assert suggestion["low"] == 150.0
    assert suggestion["high"] == 250.0


async def test_pending_listings_are_not_evidence_of_a_going_rate(
    client: AsyncClient,
    campus: dict[str, str],
    make_user: UserFactory,
    make_listing: ListingFactory,
    admin: Actor,
) -> None:
    seller = await make_user(campus)
    for price in (100, 200, 300):
        await make_listing(
            seller,
            course_code="CSE470",
            edition="1st Edition",
            listing_type="SELL",
            price=price,
        )

    # All three are still in the moderation queue, so none of them is a price
    # anybody has agreed to look at, let alone pay.
    suggestion = await _suggest(client, seller, course_code="CSE470")
    assert suggestion["basis"] == "insufficient_data"
    assert suggestion["sample_size"] == 0


async def test_free_listings_have_nothing_to_suggest(
    client: AsyncClient, campus: dict[str, str], make_user: UserFactory
) -> None:
    student = await make_user(campus)

    suggestion = await _suggest(
        client, student, course_code="CSE470", listing_type="FREE"
    )
    assert suggestion["basis"] == "insufficient_data"
    assert suggestion["suggested"] is None
