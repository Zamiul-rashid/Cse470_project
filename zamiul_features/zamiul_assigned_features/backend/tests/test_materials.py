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

-----------------------------------------------------------------------------
VIVA NOTES -- how to read this file
-----------------------------------------------------------------------------
Every test here is an ``async def`` and there is no ``@pytest.mark.asyncio``
above any of them. That is on purpose: ``backend/pytest.ini`` sets
``asyncio_mode = auto``, so pytest treats every async test as a coroutine to
await. An opt-in marker on 150 tests is 150 chances to forget one and have it
silently pass without ever running.

The names in each signature (``client``, ``campus``, ``make_user``,
``make_listing``, ``admin``) are **fixtures** defined in ``tests/conftest.py``.
pytest matches them by name and builds them before the test body runs. The two
that matter most:

* ``client``  -- an httpx ``AsyncClient`` bound straight to the FastAPI app
                 (no real network), whose base URL already carries ``/api/v1``.
* ``make_listing`` -- uploads a **genuine PDF** through real multipart, and
                 optionally approves it via ``approve_as=admin``.

Each test gets its own freshly-migrated SQLite file, so nothing here depends on
anything another test did.

Run just this file with:      cd backend && pytest tests/test_materials.py -v
(If pytest fails on a missing 'lark' module, run `unset PYTHONPATH` first --
that is a ROS install leaking into the path, not a problem with this project.)
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from httpx import AsyncClient

# Actor is the dataclass conftest returns from make_user(): it bundles a
# registered account's id, name, tokens and -- most usefully -- a ready-made
# ``headers`` dict holding "Authorization: Bearer <token>".
from tests.conftest import Actor, ListingFactory, UserFactory


# ---------------------------------------------------------------------------
# tiny readability helpers
#
# Every list endpoint returns the same Page envelope:
#   {"items": [...], "total": N, "page": 1, "page_size": 20, "pages": 3}
# These two pull one column out of ``items`` so the assertions below read as
# sentences instead of nested subscripts.
# ---------------------------------------------------------------------------


def _titles(page: dict[str, Any]) -> list[str]:
    """Just the titles from a Page response, in the order the server sent them."""
    return [item["title"] for item in page["items"]]


def _ids(page: dict[str, Any]) -> list[str]:
    """Just the listing_ids from a Page response."""
    return [item["listing_id"] for item in page["items"]]


# ===========================================================================
# FR 1.1 (upload) + FR 4.1 (moderation)
#
# THE most important test in this file. It is the only place that proves the
# moderation gate actually gates: an upload exists but is unreachable until an
# admin approves it. If this passes, "nothing is public until a moderator says
# so" is a fact rather than a claim.
# ===========================================================================


async def test_upload_lands_pending_and_only_appears_once_approved(
    client: AsyncClient,
    campus: dict[str, str],
    make_user: UserFactory,
    make_listing: ListingFactory,
    admin: Actor,
) -> None:
    # Two separate accounts: one posts, one browses. The second one is the
    # whole point -- an uploader can always see their own PENDING listing, so
    # testing visibility with a single account would prove nothing.
    uploader = await make_user(campus)
    reader = await make_user(campus)

    # make_listing does a real multipart POST /materials with a valid PDF.
    # No approve_as= here, so it stays in whatever state the API gives it.
    listing = await make_listing(
        uploader, title="Thermodynamics Lecture Notes", course_code="MEC201"
    )
    # (1) The upload landed in the moderation queue, not live on the site.
    assert listing["status"] == "PENDING"
    # (2) storage.generate_preview() succeeded -- the 3-page extract exists.
    #     Note this is has_preview (a bool), never preview_path: the path is
    #     deliberately absent from MaterialRead so it cannot leak.
    assert listing["has_preview"] is True
    # (3) pypdf actually parsed the file. conftest's build_pdf() defaults to
    #     one page, which is why this is 1 and not something larger.
    assert listing["page_count"] == 1

    # --- BEFORE APPROVAL: the reader cannot find it -----------------------
    before = await client.get(
        "/materials", headers=reader.headers, params={"course_code": "MEC201"}
    )
    assert before.status_code == 200
    # total == 0, not "the listing is in items with a PENDING badge". A
    # non-owner's search is filtered in SQL by PUBLIC_LISTING_STATUSES, so the
    # row never even reaches the serializer.
    assert before.json()["total"] == 0

    # --- THE ADMIN CLICKS APPROVE ----------------------------------------
    # This is the FR 4.1 endpoint. admin.headers carries a token for a user
    # whose users.role was promoted to 'ADMIN' by the make_admin fixture.
    approved = await client.post(
        f"/admin/materials/{listing['listing_id']}/approve",
        headers=admin.headers,
        json={"note": "Legible scan."},
    )
    assert approved.status_code == 200, approved.text
    # The response is ApprovalResult -- {"listing": {...}, "notified": N} --
    # so the listing is nested one level down.
    assert approved.json()["listing"]["status"] == "APPROVED"

    # --- AFTER APPROVAL: the same search now finds it ---------------------
    after = await client.get(
        "/materials", headers=reader.headers, params={"course_code": "MEC201"}
    )
    assert after.json()["total"] == 1
    # And it is the *same* listing, not merely "some listing appeared".
    assert _ids(after.json()) == [listing["listing_id"]]


# ===========================================================================
# FR 1.2 -- keyword search
#
# Proves the query really reaches the FTS5 virtual table. Without the prefix
# assertion below, a plain LIKE '%thermodynamics%' would pass the first half
# of this test and nobody would notice the index was dead.
# ===========================================================================


async def test_keyword_search_hits_the_fts_index(
    client: AsyncClient,
    campus: dict[str, str],
    make_user: UserFactory,
    make_listing: ListingFactory,
    admin: Actor,
) -> None:
    uploader = await make_user(campus)
    # Both approved, so both are publicly searchable. approve_as=admin does the
    # upload AND the approval in one call.
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

    # A whole-word hit. ?q= goes through _keyword_condition() in materials.py,
    # which runs "SELECT rowid FROM materials_fts WHERE materials_fts MATCH ?"
    # and feeds the rowids back into the main query as an IN clause.
    hit = await client.get(
        "/materials", headers=uploader.headers, params={"q": "thermodynamics"}
    )
    assert hit.status_code == 200, hit.text
    # Exactly one result, and it is the right one -- the other listing shares
    # no tokens with the query.
    assert _titles(hit.json()) == ["Thermodynamics Lecture Notes"]

    # Tokens are indexed for prefix search so the grid updates while the student
    # is still typing.
    # "discre" is not a word in any title. It only matches because
    # _fts_expression() appends '*' to each token, producing '"discre"*'.
    # A LIKE fallback would also match here -- but combined with the exact hit
    # above and the miss below, the three together only make sense if FTS5 is
    # really doing the work.
    prefix = await client.get(
        "/materials", headers=uploader.headers, params={"q": "discre"}
    )
    assert _titles(prefix.json()) == ["Discrete Mathematics Solutions"]

    # A genuine miss returns an empty page, not an error and not everything.
    miss = await client.get(
        "/materials", headers=uploader.headers, params={"q": "cryptography"}
    )
    assert miss.json()["total"] == 0


# ===========================================================================
# FR 1.2 + FR 1.3 -- filtering
#
# The course-code half is really a test of core/util.py::normalize_course_code,
# which is the single column every one of Zamiul's four features joins on.
# ===========================================================================


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
        course_code="CSE470",          # same course, written differently
        listing_type="FREE",
        price=None,                    # FREE must carry no price (DB CHECK)
        approve_as=admin,
    )
    # A third listing on an unrelated course, so "the filter returned 2" means
    # the filter worked rather than the table only holding 2 rows.
    await make_listing(
        uploader,
        title="Algorithms Handbook",
        course_code="CSE221",
        approve_as=admin,
    )

    # Queried as "cse-470": lowercase AND a hyphen. All three spellings
    # ("CSE 470", "CSE470", "cse-470") collapse to CSE470 in course_code_norm,
    # so the two SE listings come back and the CSE221 one does not.
    by_course = await client.get(
        "/materials", headers=uploader.headers, params={"course_code": "cse-470"}
    )
    assert by_course.json()["total"] == 2
    # A set, not a list: the order depends on the default sort and is not what
    # this test is about.
    assert set(_ids(by_course.json())) == {sold["listing_id"], freebie["listing_id"]}

    # FR 1.3 -- stacking a listing_type filter narrows the same two to one.
    by_type = await client.get(
        "/materials",
        headers=uploader.headers,
        params={"course_code": "CSE470", "listing_type": "FREE"},
    )
    assert _ids(by_type.json()) == [freebie["listing_id"]]
    # And the FREE listing really has no price, all the way out to the wire.
    assert by_type.json()["items"][0]["price"] is None


# ===========================================================================
# FR 1.1 + FR 1.3 -- the price/listing-type rule
#
# The rule lives in THREE places, deliberately:
#   1. schemas/material.py::_price_matches_type  -> a 422 naming the field
#   2. the study_materials CHECK constraint      -> the last line of defence
#   3. UploadPage.tsx clears the price field     -> the UI never sends a bad pair
# This test pins down layer 1, because that is the one the user actually sees.
# ===========================================================================


async def test_price_and_listing_type_must_agree(
    client: AsyncClient,
    campus: dict[str, str],
    make_user: UserFactory,
    make_pdf: Callable[..., bytes],
) -> None:
    uploader = await make_user(campus)
    # make_pdf is the session-scoped PDF builder from conftest. A real PDF is
    # needed even though both requests are expected to fail -- otherwise a 415
    # from the magic-byte check would mask the 422 we are actually testing.
    files = {"file": ("notes.pdf", make_pdf(), "application/pdf")}
    base = {
        "title": "Constrained Listing",
        "course_code": "CSE470",
        "department": "CSE",
        "semester": "Fall 2025",
    }

    # Case 1: FREE cannot carry a price.
    priced_free = await client.post(
        "/materials",
        headers=uploader.headers,
        data={**base, "listing_type": "FREE", "price": "200"},
        files={"file": ("notes.pdf", make_pdf(), "application/pdf")},
    )
    assert priced_free.status_code == 422
    # The message must name the offending field. A bare "422 Unprocessable"
    # tells the student nothing about which box to fix.
    assert "price" in priced_free.json()["detail"].lower()

    # Case 2: the mirror image -- SELL must carry one.
    unpriced_sale = await client.post(
        "/materials",
        headers=uploader.headers,
        data={**base, "listing_type": "SELL"},
        files=files,
    )
    assert unpriced_sale.status_code == 422
    assert "price" in unpriced_sale.json()["detail"].lower()

    # Nothing was written, so no orphaned blob and no half-listing.
    # This is the assertion that proves create_material() validates the
    # METADATA BEFORE touching the file stream. If the order were reversed,
    # both requests above would have left a 25 MB file in storage/materials/
    # that no database row will ever reference or clean up.
    listings = await client.get(
        "/materials",
        headers=uploader.headers,
        # An owner may filter their own listings by status; a stranger asking
        # this same question gets a 403 (see test_authz.py).
        params={"uploader_id": uploader.user_id, "status": "PENDING"},
    )
    assert listings.json()["total"] == 0


# ===========================================================================
# FR 1.1 -- upload security
#
# The single most quotable test in the suite: the file is NAMED notes.pdf and
# DECLARED as application/pdf, and it is still refused, because
# storage.detect_content_type() reads the actual leading bytes.
# ===========================================================================


async def test_non_pdf_upload_is_refused(
    client: AsyncClient, campus: dict[str, str], make_user: UserFactory
) -> None:
    uploader = await make_user(campus)

    # The declared content type says PDF; the bytes say otherwise, and the bytes
    # are what decide.
    # b"MZ\x90\x00" is the DOS/PE header -- i.e. a Windows .exe. It matches none
    # of the three signatures in storage._MAGIC (%PDF-, \x89PNG, \xff\xd8\xff).
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
    # 415 Unsupported Media Type -- not 400, not 422. The request was
    # well-formed; the *media* is the problem.
    assert response.status_code == 415


# ===========================================================================
# FR 1.4 -- the preview door
#
# There are two ways bytes leave the server, and they have different locks.
# This test is the open one: any signed-in student may read the 3-page extract.
# ===========================================================================


async def test_preview_is_open_to_any_signed_in_student(
    client: AsyncClient,
    campus: dict[str, str],
    make_user: UserFactory,
    make_listing: ListingFactory,
    admin: Actor,
) -> None:
    uploader = await make_user(campus)
    reader = await make_user(campus)
    # pages=5 so the preview (capped at settings.preview_pages = 3) is
    # genuinely shorter than the original. With a 1-page PDF the size
    # comparison below would be meaningless.
    listing = await make_listing(uploader, pages=5, approve_as=admin)

    # A stranger -- not the uploader, no transaction -- gets the preview.
    response = await client.get(
        f"/materials/{listing['listing_id']}/preview", headers=reader.headers
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/pdf")
    # A real PDF came back, not a JSON error page with a 200 on it.
    assert response.content.startswith(b"%PDF")

    # A separate, shorter document -- not the full file with pages hidden.
    # THIS is the assertion that makes "preview before you buy" mean something.
    # If the preview were the same file with pages hidden in the viewer, the
    # whole document would still be sitting in the browser's network tab.
    assert len(response.content) < len(
        (
            await client.get(
                f"/materials/{listing['listing_id']}/file", headers=uploader.headers
            )
        ).content
    )

    # No Authorization header at all -> 401. Note 401 (who are you?) rather
    # than 403 (you may not) -- see test_authz.py for why that distinction is
    # not cosmetic.
    assert (await client.get(f"/materials/{listing['listing_id']}/preview")).status_code == 401


# ===========================================================================
# FR 1.4 -- the full-file door
#
# The locked one. Three ways in and no fourth:
#   * the uploader, who owns the material
#   * an admin, who must be able to inspect what a report is about
#   * the buyer or seller of a COMPLETED transaction for this listing
# ===========================================================================


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

    # 403, not 404: the stranger can already SEE this listing (it is approved),
    # so hiding its existence would be pointless. What they may not do is
    # download it.
    refused = await client.get(
        f"/materials/{listing['listing_id']}/file", headers=stranger.headers
    )
    assert refused.status_code == 403
    # The message must say what they would need to BE, not just "denied" --
    # otherwise the student has no idea the download unlocks after a purchase.
    assert "completed transaction" in refused.json()["detail"]

    # The uploader always has their own file.
    allowed = await client.get(
        f"/materials/{listing['listing_id']}/file", headers=uploader.headers
    )
    assert allowed.status_code == 200
    assert allowed.content.startswith(b"%PDF")
    # "attachment" makes the browser download it; the preview route uses
    # "inline" so react-pdf can render it in the page instead.
    assert "attachment" in allowed.headers["content-disposition"]


# ===========================================================================
# FR 1.1 + FR 4.1 -- editing re-enters the queue
#
# Moderation approved a PARTICULAR title, course and description. Letting the
# uploader swap them afterwards would make approval meaningless, so an edit
# sends the listing back to PENDING.
# ===========================================================================


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
    # The note make_listing passes when approving. Proving it is stored here is
    # what makes the "cleared" assertion below meaningful.
    assert listing["moderation_note"] == "Looks fine."

    # A PATCH with only the fields being changed -- MaterialUpdate has every
    # field optional, and the router merges the changes over the existing row
    # before re-validating the price/type pair.
    edited = await client.patch(
        f"/materials/{listing['listing_id']}",
        headers=uploader.headers,
        json={"title": "Networks Lecture Notes (revised)", "price": 275},
    )
    assert edited.status_code == 200, edited.text
    body = edited.json()

    # Moderation approved a particular title; changing it re-enters the queue.
    assert body["status"] == "PENDING"
    # The edit itself still applied -- it was not silently rejected.
    assert body["title"] == "Networks Lecture Notes (revised)"
    assert body["price"] == 275
    # The old decision goes with it -- a stale "approved by X" on a queued
    # listing reads as a bug to the next moderator.
    assert body["moderated_at"] is None
    assert body["moderation_note"] is None

    # And the consequence that actually matters to users: the listing drops
    # out of everyone else's search results until it is approved again.
    hidden = await client.get(
        "/materials", headers=reader.headers, params={"course_code": "CSE321"}
    )
    assert hidden.json()["total"] == 0
