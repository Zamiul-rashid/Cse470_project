"""Rental due-date reminders (FR 3.2).

The requirement is not "send reminders", it is **send each reminder once**. Two
properties hold this file together, and the second is the one that breaks in
front of an audience:

* a rental lands in the bucket its due date calls for -- T-3, T-1 or overdue --
  counted in calendar days, the same unit ``/rentals/me`` renders;
* running the job again changes nothing. The scheduler fires daily and the
  process restarts every time somebody saves a file, so without the
  ``last_reminder_stage`` guard one student is told four times that their book
  is due tomorrow and stops reading the bell entirely.

Two things are therefore asserted everywhere: what the job *did*, from the
counts it returns, and what the recipient can actually *see*, from
``GET /notifications``. A job that returns ``notified=3`` and writes nothing is
a passing test in a suite that only reads one of them.

Due dates are written straight into ``rentals``. No endpoint accepts one -- the
term is a length, not a date, and the clock is rebased onto the physical handoff
-- so a rental that is already two days late is a state the API cannot be walked
into at any speed. That is exactly what conftest's factories plus a direct
session are for.

``send_due_reminders`` takes ``today`` because the job's own docstring says a
test should pin it. Every run below pins it to :func:`anchor`, so a suite that
happens to start at 23:59:59 buckets against the same day the fixture used.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import select

from app.core.util import utcnow
from app.jobs.rental_reminders import (
    run_rental_reminders,
    send_due_reminders,
    stage_for,
)
from app.models import (
    ListingType,
    Notification,
    NotificationType,
    Rental,
    ReminderStage,
    TransactionStatus,
)
from tests.conftest import Actor, ListingFactory, TransactionFactory, UserFactory

#: Days from today to the due date -> the stage that day must produce. ``None``
#: is "not news yet": the control case that proves the job is bucketing rather
#: than notifying everything it can see.
BUCKETS: dict[int, ReminderStage | None] = {
    5: None,
    3: ReminderStage.T_MINUS_3,
    2: ReminderStage.T_MINUS_3,
    1: ReminderStage.T_MINUS_1,
    0: ReminderStage.T_MINUS_1,
    -2: ReminderStage.OVERDUE,
}

#: Which notification each stage becomes. An upcoming return and a late one are
#: different events with different icons; one type for both would put a "due
#: soon" bell on a book that is already a week late.
NOTIFICATION_TYPES: dict[ReminderStage, NotificationType] = {
    ReminderStage.T_MINUS_3: NotificationType.RENTAL_DUE,
    ReminderStage.T_MINUS_1: NotificationType.RENTAL_DUE,
    ReminderStage.OVERDUE: NotificationType.RENTAL_OVERDUE,
}

#: The two types this job owns. Everything else on the borrower's bell --
#: TRANSACTION_UPDATE from the handoff, MODERATION_RESULT -- is another
#: module's, and counting it here would hide a reminder that never fired.
REMINDER_TYPES: tuple[str, ...] = (
    NotificationType.RENTAL_DUE.value,
    NotificationType.RENTAL_OVERDUE.value,
)


@dataclass(slots=True)
class Loan:
    """One book out on loan, with the ids both halves of the job key on."""

    days_out: int
    transaction_id: str
    rental_id: str


LoanFactory = Callable[..., Awaitable[Loan]]


# ---------------------------------------------------------------------------
# arranging loans
# ---------------------------------------------------------------------------


@pytest.fixture
def anchor() -> datetime:
    """One "now" for the whole test, from the job's own clock.

    ``app.core.util.utcnow`` is what ``send_due_reminders`` defaults ``today``
    to, so anchoring the due dates on it means the arithmetic in the test and
    the arithmetic in the job are the same subtraction.
    """
    return utcnow()


@pytest.fixture
async def lender(campus: dict[str, str], make_user: UserFactory) -> Actor:
    return await make_user(campus, name="Lender")


@pytest.fixture
async def borrower(campus: dict[str, str], make_user: UserFactory) -> Actor:
    """The renter -- the buyer of the transaction, and the only person a
    reminder is ever addressed to."""
    return await make_user(campus, name="Borrower")


@pytest.fixture
def lend(
    anchor: datetime,
    admin: Actor,
    lender: Actor,
    borrower: Actor,
    make_listing: ListingFactory,
    make_transaction: TransactionFactory,
    session_factory: async_sessionmaker[AsyncSession],
) -> LoanFactory:
    """Put one book in the borrower's hands, due ``days_out`` from the anchor.

    Everything up to the due date goes through the real API: a RENT listing,
    approved, then a transaction carrying ``rental_days`` -- which is what
    writes the ``rentals`` row at all (see services/transactions.py). Only the
    date itself is poked in, because a term is the one thing the API stores as
    a length rather than accepting as a date.
    """

    async def _lend(
        days_out: int,
        *,
        status: TransactionStatus | str = TransactionStatus.REQUESTED,
    ) -> Loan:
        listing = await make_listing(
            lender,
            title=f"Discrete Mathematics Notes, copy {days_out}",
            listing_type=ListingType.RENT,
            price=150.0,
            approve_as=admin,
        )
        transaction = await make_transaction(
            borrower, listing, lender, rental_days=14, status=status
        )

        async with session_factory() as open_session:
            rental = (
                await open_session.execute(
                    select(Rental).where(
                        Rental.transaction_id == transaction["transaction_id"]
                    )
                )
            ).scalar_one()
            rental.due_date = anchor + timedelta(days=days_out)
            open_session.add(rental)
            await open_session.commit()
            return Loan(
                days_out=days_out,
                transaction_id=transaction["transaction_id"],
                rental_id=rental.rental_id,
            )

    return _lend


@pytest.fixture
async def loans(lend: LoanFactory) -> dict[int, Loan]:
    """One live rental per bucket, all unreturned, all for the same borrower."""
    return {days_out: await lend(days_out) for days_out in BUCKETS}


# ---------------------------------------------------------------------------
# reading the two sides of the result
# ---------------------------------------------------------------------------


async def _run(
    session_factory: async_sessionmaker[AsyncSession], today: date
) -> dict[str, int]:
    """One pass of the job, on a session of its own.

    A fresh session per run is what a restarted process does, and it is the
    only way the guard is genuinely read back from the database instead of from
    an identity map that still remembers the previous pass.
    """
    async with session_factory() as open_session:
        return await send_due_reminders(open_session, today=today)


async def _stages(
    session_factory: async_sessionmaker[AsyncSession],
) -> dict[str, str]:
    """``rental_id -> last_reminder_stage``. No endpoint exposes the column;
    it is internal bookkeeping, and it is the whole of the restart defence."""
    async with session_factory() as open_session:
        rows = (
            await open_session.execute(
                select(Rental.rental_id, Rental.last_reminder_stage)
            )
        ).all()
    return {rental_id: stage for rental_id, stage in rows}


async def _reminders(client: AsyncClient, actor: Actor) -> list[dict[str, Any]]:
    """The reminders on one person's bell, through the real read path."""
    response = await client.get(
        "/notifications", headers=actor.headers, params={"page_size": 100}
    )
    assert response.status_code == 200, response.text
    return [
        item for item in response.json()["items"] if item["type"] in REMINDER_TYPES
    ]


# ---------------------------------------------------------------------------
# the buckets
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("days_remaining", "expected"),
    [
        (4, None),
        (3, ReminderStage.T_MINUS_3),
        (2, ReminderStage.T_MINUS_3),
        (1, ReminderStage.T_MINUS_1),
        (0, ReminderStage.T_MINUS_1),
        (-1, ReminderStage.OVERDUE),
    ],
    ids=["4-quiet", "3-first-warning", "2-still-t3", "1-tomorrow", "0-today", "-1-late"],
)
def test_the_bucket_edges_are_where_the_job_says_they_are(
    days_remaining: int, expected: ReminderStage | None
) -> None:
    """The two-day-wide buckets, at the exact days the boundaries sit on.

    Pinned here rather than as one rental per day because 4-vs-3 and 0-vs--1
    are where an off-by-one hides, and a rental costs an upload, an approval
    and a transaction to arrange. The end-to-end test below proves those
    buckets are actually wired to the stored due dates.
    """
    assert stage_for(days_remaining) is expected


async def test_one_pass_puts_every_rental_in_exactly_its_bucket(
    client: AsyncClient,
    anchor: datetime,
    borrower: Actor,
    loans: dict[int, Loan],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    counts = await _run(session_factory, anchor.date())

    assert counts["scanned"] == len(BUCKETS)
    assert counts["notified"] == 5
    # The rental due in five days is seen and deliberately left alone -- more
    # than three days out is not news.
    assert counts["skipped"] == 1
    assert counts[ReminderStage.T_MINUS_3.value] == 2  # due in 3 days, and in 2
    assert counts[ReminderStage.T_MINUS_1.value] == 2  # due tomorrow, and today
    assert counts[ReminderStage.OVERDUE.value] == 1

    # The stage column is the guard the next run reads, so a rental notified at
    # the wrong stage is a rental that will be chased again tomorrow.
    stages = await _stages(session_factory)
    assert {loan.days_out: stages[loan.rental_id] for loan in loans.values()} == {
        days_out: (stage.value if stage is not None else ReminderStage.NONE.value)
        for days_out, stage in BUCKETS.items()
    }

    # ref_id is the transaction, which is what makes each notification
    # attributable to one rental rather than to "a book, somewhere".
    by_transaction = {item["ref_id"]: item for item in await _reminders(client, borrower)}
    assert set(by_transaction) == {
        loan.transaction_id
        for days_out, loan in loans.items()
        if BUCKETS[days_out] is not None
    }

    for days_out, loan in loans.items():
        stage = BUCKETS[days_out]
        if stage is None:
            continue
        notification = by_transaction[loan.transaction_id]
        assert notification["type"] == NOTIFICATION_TYPES[stage].value
        # 'transaction', not 'rental': the transaction page is the only place
        # the renter can see the counterparty and mark the book returned.
        assert notification["ref_type"] == "transaction"

    # The count is rendered from days_remaining, the one number the buckets
    # throw away -- "overdue" alone does not tell a student how late they are.
    assert "2 days overdue" in by_transaction[loans[-2].transaction_id]["message"]


async def test_the_reminder_goes_to_the_renter_not_the_lender(
    client: AsyncClient,
    anchor: datetime,
    lender: Actor,
    borrower: Actor,
    loans: dict[int, Loan],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The renter holds the book; the lender is not the one who has to act."""
    await _run(session_factory, anchor.date())

    assert len(await _reminders(client, borrower)) == 5
    assert await _reminders(client, lender) == []

    # Read across every user, not just those two: a job that also copied in a
    # third party would still leave the lender's bell clean.
    async with session_factory() as open_session:
        recipients = set(
            (
                await open_session.execute(
                    select(Notification.user_id).where(
                        Notification.type.in_(REMINDER_TYPES)
                    )
                )
            )
            .scalars()
            .all()
        )
    assert recipients == {borrower.user_id}


# ---------------------------------------------------------------------------
# the restart guard -- the central guarantee
# ---------------------------------------------------------------------------


async def test_a_second_run_notifies_nobody_twice(
    client: AsyncClient,
    anchor: datetime,
    borrower: Actor,
    loans: dict[int, Loan],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Nothing has changed, so nothing more may be sent.

    This is what stops a process restart -- or simply tomorrow morning --
    re-spamming every renter on the platform.
    """
    first = await _run(session_factory, anchor.date())
    stages_after_first = await _stages(session_factory)
    bell_after_first = [item["notification_id"] for item in await _reminders(client, borrower)]

    second = await _run(session_factory, anchor.date())

    assert first["notified"] == 5
    assert second["notified"] == 0
    # Nothing dropped out of the scan: every rental is still open, still
    # unreturned and still inside a bucket. They are refused on the guard, not
    # filtered out by the query, which is the only version of this that also
    # holds for a rental sitting at T-1 for two days running.
    assert second["scanned"] == len(BUCKETS)
    assert second["skipped"] == len(BUCKETS)

    assert await _stages(session_factory) == stages_after_first
    # By id, not by count: a run that deleted one reminder and wrote another
    # keeps the count identical.
    assert [
        item["notification_id"] for item in await _reminders(client, borrower)
    ] == bell_after_first


async def test_a_rental_that_goes_overdue_is_chased_again(
    client: AsyncClient,
    anchor: datetime,
    borrower: Actor,
    lend: LoanFactory,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The guard is "at or past this stage", not "ever notified".

    A rental already warned at T-3 that then goes late is new information --
    silence here would mean the only rentals ever reported overdue are the ones
    nobody warned in the first place.
    """
    loan = await lend(2)

    first = await _run(session_factory, anchor.date())
    assert first[ReminderStage.T_MINUS_3.value] == 1

    # Four days later. Nothing about the rental changed -- only the clock.
    later = (anchor + timedelta(days=4)).date()
    second = await _run(session_factory, later)
    assert second["notified"] == 1
    assert second[ReminderStage.OVERDUE.value] == 1

    assert (await _stages(session_factory))[loan.rental_id] == ReminderStage.OVERDUE.value

    bell = await _reminders(client, borrower)
    # Newest first. Two reminders about one rental, and the second one says
    # something the first did not.
    assert [item["type"] for item in bell] == [
        NotificationType.RENTAL_OVERDUE.value,
        NotificationType.RENTAL_DUE.value,
    ]
    assert {item["ref_id"] for item in bell} == {loan.transaction_id}

    # The guard moved up with it: OVERDUE is the last stage, so the chasing
    # stops there rather than repeating every morning until the book comes back.
    assert (await _run(session_factory, later))["notified"] == 0


async def test_a_returned_rental_is_never_chased_however_overdue(
    client: AsyncClient,
    borrower: Actor,
    lend: LoanFactory,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    loan = await lend(-30, status=TransactionStatus.COMPLETED)

    returned = await client.post(
        f"/rentals/{loan.rental_id}/return", headers=borrower.headers
    )
    assert returned.status_code == 200, returned.text
    assert returned.json()["returned"] is True

    # run_rental_reminders(), not send_due_reminders(): this is the no-argument
    # entry point APScheduler calls, and it opens its own SessionLocal. A month
    # overdue is unambiguous on any clock, so nothing here needs pinning.
    counts = await run_rental_reminders()

    # Excluded by the WHERE clause, not by the stage guard. A returned rental
    # is not a candidate at all, so it can never be one stage away from being
    # chased for a book its owner already has back.
    assert counts["scanned"] == 0
    assert counts["notified"] == 0
    assert await _reminders(client, borrower) == []
