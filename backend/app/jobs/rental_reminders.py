"""Daily rental due-date reminders (FR 3.2).

The requirement is not "send reminders", it is "send each reminder once". A
scheduler that re-scans the same open rentals every morning -- and a process
that restarts three times during a demo -- will happily tell a student four
times that their book is due tomorrow, which trains them to ignore the bell.

``rentals.last_reminder_stage`` is the entire defence. Each rental walks
NONE -> T_MINUS_3 -> T_MINUS_1 -> OVERDUE at most once, and the guard compares
against ``REMINDER_STAGE_ORDER`` rather than testing equality, so a rental that
was never reminded at T-3 (uploaded late, machine switched off) still gets its
T-1 warning instead of being stuck waiting for a stage that can no longer fire.

The buckets are deliberately two days wide. The job runs once a day; if the
machine was asleep on the exact morning a rental was three days out, a
point-in-time test would skip that stage forever. A range degrades a missed day
into a slightly late reminder rather than a silent one.

:func:`run_rental_reminders` opens its own session and is safe to call straight
from a test, a REPL or the scheduler -- see the README's "to see the rental
reminder job fire without waiting for 8am".
"""

from __future__ import annotations

import logging
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.db import SessionLocal
from app.core.util import utcnow
from app.models import (
    REMINDER_STAGE_ORDER,
    NotificationType,
    Rental,
    ReminderStage,
    StudyMaterial,
    Transaction,
)
from app.services.notifier import notify

logger = logging.getLogger(__name__)

#: Days-remaining boundaries, widest bucket first. See the module docstring for
#: why these are ranges and not exact days.
_T_MINUS_3_FROM = 2
_T_MINUS_3_TO = 3


def stage_for(days_remaining: int) -> ReminderStage | None:
    """Which reminder a rental this far from its due date deserves.

    ``None`` means "not yet" -- more than three days out is not news.
    """
    if days_remaining > _T_MINUS_3_TO:
        return None
    if days_remaining >= _T_MINUS_3_FROM:
        return ReminderStage.T_MINUS_3
    if days_remaining >= 0:
        return ReminderStage.T_MINUS_1
    return ReminderStage.OVERDUE


def _message(title: str, days_remaining: int) -> str:
    """Phrase the reminder in the tense the reader is actually in."""
    if days_remaining < 0:
        overdue_by = -days_remaining
        unit = "day" if overdue_by == 1 else "days"
        return f"'{title}' is {overdue_by} {unit} overdue. Please return it."
    if days_remaining == 0:
        return f"'{title}' is due back today."
    if days_remaining == 1:
        return f"'{title}' is due back tomorrow."
    return f"'{title}' is due back in {days_remaining} days."


async def send_due_reminders(
    session: AsyncSession, *, today: date | None = None
) -> dict[str, int]:
    """The body of the job, with the session and the date injected.

    Split out from :func:`run_rental_reminders` so a test can hand it the
    fixture session and pin "today" to a date that makes all three stages fire,
    without patching the clock for the whole process.
    """
    today = today or utcnow().date()
    counts: dict[str, int] = {
        "scanned": 0,
        "notified": 0,
        "skipped": 0,
        ReminderStage.T_MINUS_3.value: 0,
        ReminderStage.T_MINUS_1.value: 0,
        ReminderStage.OVERDUE.value: 0,
    }

    # One explicit join, no lazy loading: the transaction carries the renter
    # (buyer_id) and the listing carries the title the reminder has to name.
    # An outer join on the listing keeps a rental for a purged listing from
    # vanishing from the scan -- the book is still out there.
    stmt = (
        select(Rental, Transaction.buyer_id, Transaction.transaction_id, StudyMaterial.title)
        .join(Transaction, Transaction.transaction_id == Rental.transaction_id)
        .join(
            StudyMaterial,
            StudyMaterial.listing_id == Transaction.listing_id,
            isouter=True,
        )
        .where(Rental.returned.is_(False))
        .order_by(Rental.due_date)
    )

    for rental, buyer_id, transaction_id, title in (await session.execute(stmt)).all():
        counts["scanned"] += 1

        # The rental clock is rebased onto the physical handoff by
        # services.transactions, so due_date is always the honest one.
        days_remaining = (rental.due_date.date() - today).days
        stage = stage_for(days_remaining)
        if stage is None:
            counts["skipped"] += 1
            continue

        # The restart guard. Anything already at or past this stage has had its
        # reminder; without this line every process restart re-sends the lot.
        current = REMINDER_STAGE_ORDER.get(rental.last_reminder_stage, 0)
        if current >= REMINDER_STAGE_ORDER[stage]:
            counts["skipped"] += 1
            continue

        notification_type = (
            NotificationType.RENTAL_OVERDUE
            if stage == ReminderStage.OVERDUE
            else NotificationType.RENTAL_DUE
        )
        await notify(
            session,
            user_id=buyer_id,
            type=notification_type,
            message=_message(title or "Your rental", days_remaining),
            # 'transaction', not 'rental': the transaction page is where the
            # renter can actually see the counterparty and mark it returned.
            ref_type="transaction",
            ref_id=transaction_id,
            commit=False,
        )

        # ``.value``, never the member: on a ``str`` Enum under 3.11 the naive
        # conversion writes 'ReminderStage.T_MINUS_1' into the column and the
        # CHECK constraint rejects it.
        rental.last_reminder_stage = stage.value
        session.add(rental)
        # Commit per rental, not once at the end: the stage write and the
        # notification it announces land together, and SQLite's single writer
        # is held for one row at a time rather than the whole scan.
        await session.commit()

        counts["notified"] += 1
        counts[stage.value] += 1

    logger.info(
        "rental reminders: scanned=%(scanned)d notified=%(notified)d skipped=%(skipped)d",
        counts,
    )
    return counts


async def run_rental_reminders() -> dict[str, int]:
    """Scheduler entry point. Opens its own session; returns the tally.

    Takes no arguments so APScheduler can call it directly, and returns counts
    rather than logging only -- the README's one-liner prints them, and a test
    asserts on them.
    """
    async with SessionLocal() as session:
        return await send_due_reminders(session)


__all__ = ["run_rental_reminders", "send_due_reminders", "stage_for"]
