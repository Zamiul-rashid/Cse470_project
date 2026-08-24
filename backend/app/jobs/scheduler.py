"""APScheduler wiring for the one background job this system has (FR 3.2).

The scheduler lives *inside* the FastAPI process and shares its event loop --
``AsyncIOScheduler``, not the threaded variant -- because the job it runs is an
async function talking to the same async engine as every router. That has one
hard consequence: **the app must run
with ``uvicorn --workers 1``**. Four workers means four schedulers, and every
student gets told four times that their rental is due.

``enable_scheduler`` exists for the test suite. A background job that fires
mid-test writes notification rows nobody asked for and makes assertions flaky,
so ``ENABLE_SCHEDULER=false`` keeps the whole thing out of the way -- and
``run_rental_reminders()`` remains directly callable, which is how the job is
actually tested.
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.config import settings
from app.jobs.rental_reminders import run_rental_reminders

logger = logging.getLogger(__name__)

#: Stable id so ``replace_existing`` can do its job across a reload.
RENTAL_REMINDER_JOB_ID = "rental-reminders"

#: A reminder that missed its 08:00 slot because the laptop was shut is still
#: worth sending when it wakes up; one that is half a day late is not.
_MISFIRE_GRACE_SECONDS = 6 * 60 * 60

#: The process-wide scheduler. Module state rather than an app attribute so
#: ``shutdown_scheduler`` can be called from anywhere the lifespan unwinds.
_scheduler: AsyncIOScheduler | None = None


async def _rental_reminder_job() -> None:
    """Thin wrapper so the daily tally lands in the server log.

    APScheduler discards a job's return value, and the counts are the only
    evidence at 08:00 that the job ran at all rather than raised.
    """
    counts = await run_rental_reminders()
    logger.info("rental reminder job finished: %s", counts)


def build_scheduler() -> AsyncIOScheduler:
    """A configured, *not yet started*, scheduler with the daily job attached.

    Separate from :func:`start_scheduler` so a test can inspect the trigger
    without starting a live scheduler in the test's event loop.
    """
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        _rental_reminder_job,
        CronTrigger(hour=settings.reminder_hour, minute=0),
        id=RENTAL_REMINDER_JOB_ID,
        name="Rental due-date reminders (FR 3.2)",
        replace_existing=True,
        # If several runs were missed (a laptop closed over a weekend), run
        # once on wake rather than replaying one execution per missed day --
        # the reminder-stage guard would swallow the repeats anyway, but three
        # wasted table scans at boot is three too many.
        coalesce=True,
        max_instances=1,
        misfire_grace_time=_MISFIRE_GRACE_SECONDS,
    )
    return scheduler


async def start_scheduler() -> AsyncIOScheduler | None:
    """Called from the FastAPI lifespan. ``None`` when scheduling is disabled.

    Async only so the lifespan can ``await`` it alongside its other startup
    steps; ``AsyncIOScheduler.start()`` itself is synchronous but must be
    called from inside the running loop it will schedule onto.
    """
    global _scheduler

    if not settings.enable_scheduler:
        logger.info("scheduler disabled (ENABLE_SCHEDULER=false); no jobs registered")
        return None
    if _scheduler is not None and _scheduler.running:
        # A reload that ran the lifespan twice must not double every job.
        return _scheduler

    _scheduler = build_scheduler()
    _scheduler.start()
    logger.info(
        "scheduler started: rental reminders daily at %02d:00 local time",
        settings.reminder_hour,
    )
    return _scheduler


async def shutdown_scheduler() -> None:
    """Called from the lifespan's teardown. Safe to call when never started."""
    global _scheduler

    if _scheduler is None:
        return
    if _scheduler.running:
        # wait=False: a reminder scan that is mid-flight at shutdown will be
        # redone tomorrow, and holding the process open for it delays the
        # reload the developer is waiting on.
        _scheduler.shutdown(wait=False)
    _scheduler = None
    logger.info("scheduler stopped")


def get_scheduler() -> AsyncIOScheduler | None:
    """The live scheduler, if there is one. For diagnostics and tests."""
    return _scheduler


__all__ = [
    "RENTAL_REMINDER_JOB_ID",
    "build_scheduler",
    "get_scheduler",
    "shutdown_scheduler",
    "start_scheduler",
]
