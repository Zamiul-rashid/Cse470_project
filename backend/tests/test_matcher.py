"""Auto-match: an approved listing finds the people who asked for it (FR 2.3).

Two decisions are what this file holds in place,
and both are one refactor away from being lost:

* **The trigger is approval, not creation.** A PENDING listing is invisible to
  everyone but its uploader and the moderators, so an alert about one sends the
  reader to a page that 404s. That is asserted here directly, with the 404 next
  to it, because "we notify a little early" sounds harmless right up until a
  student taps the bell.
* **Requests drive matching, not wishlists.** A ``Request`` is a forward-looking
  saved search with structured criteria, and a matched one becomes MATCHED so
  the board stops advertising a want that has been answered and the same
  request can never fire a second time.

The negative cases carry as much weight as the happy path. A matcher that
notifies everybody is indistinguishable from one that works, right up to the
moment somebody counts the notifications -- so every test here asserts three
things together: the count the admin is shown, the recipient's bell, and the
status the request board now displays.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from app.models import ListingType, NotificationType, RequestStatus
from tests.conftest import Actor, CampusFactory, ListingFactory, UserFactory

COURSE = "CSE220"
LISTING_DEPARTMENT = "CSE"
LISTING_PRICE = 450.0

#: Stand-in for "a campus that is not the listing's", resolved inside the test
#: -- a request may only name a campus that actually exists.
OTHER_CAMPUS = "<resolved from make_campus>"

#: One criterion apiece, each of them a reason this listing is not what that
#: student asked for. Everything else is left NULL, so any of these firing means
#: a filter was dropped rather than merely loosened.
MISMATCHES: dict[str, dict[str, Any]] = {
    "another course": {"course_code": "CSE221"},
    "another department": {"department": "EEE"},
    "another listing type": {"listing_type": ListingType.RENT.value},
    "under budget": {"max_price": LISTING_PRICE - 1},
    "another campus": {"campus_id": OTHER_CAMPUS},
}


# ---------------------------------------------------------------------------
# helpers -- everything goes through the endpoints a student actually uses
# ---------------------------------------------------------------------------


async def _post_request(
    client: AsyncClient, actor: Actor, **criteria: Any
) -> dict[str, Any]:
    """Post a want-ad for :data:`COURSE`, overridden by ``criteria``.

    ``POST /requests`` also scans the existing catalogue in a background task,
    so the OPEN assertion is load-bearing: a request that arrived already
    MATCHED would make every later assertion about matching meaningless.
    """
    response = await client.post(
        "/requests", headers=actor.headers, json={"course_code": COURSE, **criteria}
    )
    assert response.status_code == 201, response.text
    assert response.json()["status"] == RequestStatus.OPEN.value
    return response.json()


async def _approve(
    client: AsyncClient, admin: Actor, listing: dict[str, Any]
) -> dict[str, Any]:
    """The admin's approve click. Returns ``{listing, notified}``."""
    response = await client.post(
        f"/admin/materials/{listing['listing_id']}/approve",
        headers=admin.headers,
        json={"note": "Legible scan."},
    )
    assert response.status_code == 200, response.text
    assert response.json()["listing"]["status"] == "APPROVED"
    return response.json()


async def _notifications(
    client: AsyncClient,
    actor: Actor,
    kind: NotificationType = NotificationType.REQUEST_MATCH,
) -> list[dict[str, Any]]:
    """One person's bell, filtered to a single type.

    Filtered rather than counted whole: the uploader legitimately receives a
    MODERATION_RESULT from the same click, so an assertion that counted rows
    could pass on entirely the wrong notification.
    """
    response = await client.get(
        "/notifications", headers=actor.headers, params={"page_size": 100}
    )
    assert response.status_code == 200, response.text
    return [item for item in response.json()["items"] if item["type"] == kind.value]


async def _request_status(client: AsyncClient, actor: Actor, request_id: str) -> str:
    """What the owner's own board shows for this request.

    ``mine=true`` on purpose -- the public board hides CLOSED requests, and one
    of the tests below has to see that a closed request stayed closed.
    """
    response = await client.get(
        "/requests", headers=actor.headers, params={"mine": True}
    )
    assert response.status_code == 200, response.text
    board = {item["request_id"]: item["status"] for item in response.json()["items"]}
    return board[request_id]


@pytest.fixture
async def uploader(campus: dict[str, str], make_user: UserFactory) -> Actor:
    return await make_user(campus, name="Uploader")


@pytest.fixture
async def listing(uploader: Actor, make_listing: ListingFactory) -> dict[str, Any]:
    """One PENDING CSE220 listing: what every request here is measured against.

    It stays PENDING because approval is the event under test -- each test
    approves it at the moment it means to.
    """
    return await make_listing(
        uploader,
        title="Data Structures Complete Notes",
        course_code=COURSE,
        department=LISTING_DEPARTMENT,
        listing_type=ListingType.SELL,
        price=LISTING_PRICE,
    )


# ---------------------------------------------------------------------------
# the happy path
# ---------------------------------------------------------------------------


async def test_approval_notifies_the_holder_of_a_matching_open_request(
    client: AsyncClient,
    campus: dict[str, str],
    admin: Actor,
    listing: dict[str, Any],
    make_user: UserFactory,
) -> None:
    wanted = await make_user(campus, name="Requester")
    request = await _post_request(
        client,
        wanted,
        department=LISTING_DEPARTMENT,
        listing_type=ListingType.SELL.value,
        max_price=600,
    )

    result = await _approve(client, admin, listing)
    # The number the panel renders as "1 student notified". Without it the most
    # convincing moment in the FR 2.3 demo is invisible to the person who caused
    # it -- and a fan-out that silently sent nothing looks identical.
    assert result["notified"] == 1

    matched = await _notifications(client, wanted)
    assert len(matched) == 1
    # ref_type/ref_id are what the bell menu navigates on. Pointing at the
    # request, or at nothing, leaves the student with news and no way to act.
    assert matched[0]["ref_type"] == "listing"
    assert matched[0]["ref_id"] == listing["listing_id"]
    # The alert has to name what was found; "something matched" sends the reader
    # hunting through the whole board.
    assert listing["title"] in matched[0]["message"]

    # The want has been answered: the board stops advertising it, and this
    # request is now ineligible for every future listing.
    assert await _request_status(client, wanted, request["request_id"]) == "MATCHED"


async def test_a_pending_listing_notifies_nobody(
    client: AsyncClient,
    campus: dict[str, str],
    listing: dict[str, Any],
    make_user: UserFactory,
) -> None:
    """Approval is the trigger, not creation."""
    wanted = await make_user(campus, name="Requester")
    request = await _post_request(client, wanted)

    assert listing["status"] == "PENDING"
    assert await _notifications(client, wanted) == []
    assert await _request_status(client, wanted, request["request_id"]) == "OPEN"

    # And this is why. A notification sent now would point at a page this
    # student cannot open: the listing is between its uploader and the
    # moderators until somebody approves it.
    invisible = await client.get(
        f"/materials/{listing['listing_id']}", headers=wanted.headers
    )
    assert invisible.status_code == 404


async def test_the_uploader_is_not_matched_to_their_own_listing(
    client: AsyncClient,
    admin: Actor,
    uploader: Actor,
    listing: dict[str, Any],
) -> None:
    """Somebody who wants CSE220 notes and then posts some has not been
    answered by themselves."""
    own = await _post_request(client, uploader)

    assert (await _approve(client, admin, listing))["notified"] == 0
    assert await _notifications(client, uploader) == []
    assert await _request_status(client, uploader, own["request_id"]) == "OPEN"

    # Their approval notice still arrives, so the empty REQUEST_MATCH list above
    # is a decision the matcher made and not a notifier that is down.
    assert await _notifications(client, uploader, NotificationType.MODERATION_RESULT)


# ---------------------------------------------------------------------------
# the criteria
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("criteria", list(MISMATCHES.values()), ids=list(MISMATCHES))
async def test_a_request_that_does_not_match_stays_open_and_silent(
    client: AsyncClient,
    campus: dict[str, str],
    admin: Actor,
    listing: dict[str, Any],
    make_campus: CampusFactory,
    make_user: UserFactory,
    criteria: dict[str, Any],
) -> None:
    if criteria.get("campus_id") == OTHER_CAMPUS:
        other = await make_campus(name="Far Campus")
        criteria = {**criteria, "campus_id": other["campus_id"]}

    picky = await make_user(campus, name="Picky")
    request = await _post_request(client, picky, **criteria)

    assert (await _approve(client, admin, listing))["notified"] == 0
    assert await _notifications(client, picky) == []
    # OPEN, still: a want nobody answered has to stay on the board. Resolving it
    # here would take the ad down and leave the student waiting on a match that
    # never happened.
    assert await _request_status(client, picky, request["request_id"]) == "OPEN"


async def test_null_criteria_are_wildcards(
    client: AsyncClient,
    admin: Actor,
    uploader: Actor,
    make_campus: CampusFactory,
    make_listing: ListingFactory,
    make_user: UserFactory,
) -> None:
    """A request naming only a course is a request for that course, anywhere.

    All four optional criteria are left unset, and the listing contradicts what
    each of them would have said: another department, another campus, a type the
    student never asked for, at a price no budget was set against.
    """
    far_campus = await make_campus(name="Far Campus")
    far_student = await make_user(far_campus, name="Far Student")

    request = await _post_request(client, far_student)
    assert request["department"] is None
    assert request["campus_id"] is None
    assert request["listing_type"] is None
    assert request["max_price"] is None

    listing = await make_listing(
        uploader,
        title="Data Structures Lab Manual",
        course_code=COURSE,
        department="MAT",
        listing_type=ListingType.RENT,
        price=2500.0,
    )
    # A listing belongs to its uploader's campus, which is not the one this
    # student registered on -- so a campus filter that quietly defaulted to the
    # requester's own campus would swallow this match.
    assert listing["campus_id"] != far_campus["campus_id"]

    assert (await _approve(client, admin, listing))["notified"] == 1
    matched = await _notifications(client, far_student)
    assert len(matched) == 1
    assert matched[0]["ref_id"] == listing["listing_id"]
    assert await _request_status(client, far_student, request["request_id"]) == "MATCHED"


async def test_course_codes_match_across_spacing_and_punctuation(
    client: AsyncClient,
    campus: dict[str, str],
    admin: Actor,
    uploader: Actor,
    make_listing: ListingFactory,
    make_user: UserFactory,
) -> None:
    """``cse 220`` and ``CSE-220`` are the same course to everyone but a raw
    string comparison."""
    student = await make_user(campus, name="Requester")
    request = await _post_request(client, student, course_code="cse 220")

    listing = await make_listing(
        uploader,
        title="Data Structures Handbook",
        course_code="CSE-220",
        department=LISTING_DEPARTMENT,
        listing_type=ListingType.SELL,
        price=LISTING_PRICE,
    )

    # Both sides are stored normalised and the matcher joins on that column --
    # the strings the two students actually typed never meet.
    assert request["course_code_norm"] == COURSE
    assert listing["course_code_norm"] == COURSE

    assert (await _approve(client, admin, listing))["notified"] == 1
    assert len(await _notifications(client, student)) == 1
    assert await _request_status(client, student, request["request_id"]) == "MATCHED"


# ---------------------------------------------------------------------------
# requests that are no longer listening
# ---------------------------------------------------------------------------


async def test_a_closed_request_is_not_matched(
    client: AsyncClient,
    campus: dict[str, str],
    admin: Actor,
    listing: dict[str, Any],
    make_user: UserFactory,
) -> None:
    """Closing a request is a student saying they no longer need the book."""
    quitter = await make_user(campus, name="Quitter")
    request = await _post_request(client, quitter)

    closed = await client.patch(
        f"/requests/{request['request_id']}",
        headers=quitter.headers,
        json={"status": RequestStatus.CLOSED.value},
    )
    assert closed.status_code == 200, closed.text
    assert closed.json()["status"] == "CLOSED"

    assert (await _approve(client, admin, listing))["notified"] == 0
    assert await _notifications(client, quitter) == []
    # And not quietly reopened by a match it never asked for.
    assert await _request_status(client, quitter, request["request_id"]) == "CLOSED"


async def test_a_request_already_matched_is_not_matched_again(
    client: AsyncClient,
    campus: dict[str, str],
    admin: Actor,
    uploader: Actor,
    listing: dict[str, Any],
    make_listing: ListingFactory,
    make_user: UserFactory,
) -> None:
    """One want, one alert.

    The second listing is a *different* row, so the per-listing de-duplication
    cannot be what saves this: the only thing standing between the student and a
    second alert is their request no longer being OPEN.
    """
    wanted = await make_user(campus, name="Requester")
    request = await _post_request(client, wanted)

    assert (await _approve(client, admin, listing))["notified"] == 1
    assert await _request_status(client, wanted, request["request_id"]) == "MATCHED"

    second = await make_listing(
        uploader,
        title="Data Structures Past Papers",
        course_code=COURSE,
        department=LISTING_DEPARTMENT,
        listing_type=ListingType.SELL,
        price=LISTING_PRICE,
    )
    assert (await _approve(client, admin, second))["notified"] == 0

    matched = await _notifications(client, wanted)
    assert len(matched) == 1
    assert matched[0]["ref_id"] == listing["listing_id"]


async def test_approving_the_same_listing_twice_does_not_re_notify(
    client: AsyncClient,
    campus: dict[str, str],
    admin: Actor,
    listing: dict[str, Any],
    make_user: UserFactory,
) -> None:
    """A moderator's double-click must not re-run the fan-out.

    The 409 is the mechanism (approve is a decision on a *pending* listing), but
    the notification count is the requirement -- a second 200 here would be one
    click turning into two alerts for every requester on the course.
    """
    wanted = await make_user(campus, name="Requester")
    await _post_request(client, wanted)

    assert (await _approve(client, admin, listing))["notified"] == 1

    again = await client.post(
        f"/admin/materials/{listing['listing_id']}/approve",
        headers=admin.headers,
        json={"note": "Clicked twice."},
    )
    assert again.status_code == 409
    assert "APPROVED" in again.json()["detail"]

    assert len(await _notifications(client, wanted)) == 1
