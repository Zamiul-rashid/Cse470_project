"""Write side of the trending dashboard (FR 4.4).

``course_demand_events`` is an append-only log: one row every time a student
searches, opens, requests or bookmarks material for a course. The read side
(``services/analytics.trending_courses``) aggregates it over a rolling window
with the weights in ``models.enums.DEMAND_WEIGHTS``.

The point of splitting the write side out early -- FR 4.4 is tracked as two
separate tickets -- is that a dashboard built in week 8 over data
first collected in week 8 has nothing to show. These calls are wired into
Phase 1 endpoints so the events accumulate for a month before anyone reads
them.

**Everything here must stay cheap.** It runs on every search and every listing
view, on a database with one writer. So: no SELECT, no lookup, no validation
round trip -- one INSERT, and a normalisation that happens in Python.

``commit`` defaults to False because the caller almost always has a
transaction already open and the event should ride along with it. A read-only
endpoint that has nothing else to commit must still either pass ``commit=True``
or commit itself -- ``get_session`` does not, so an uncommitted event is
discarded when the request ends.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import Enum

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.util import normalize_course_code, utcnow
from app.models import CourseDemandEvent, DemandEventType


def _text(value: DemandEventType | str) -> str:
    """Store the enum's *value*, never ``str(member)``.

    Under Python 3.11 ``str()`` on a ``(str, Enum)`` member renders
    ``'DemandEventType.SEARCH'``, which would fail the column's CHECK
    constraint and silently break every aggregation downstream.
    """
    return value.value if isinstance(value, Enum) else value


async def record(
    session: AsyncSession,
    *,
    course_code: str | None,
    event_type: DemandEventType | str,
    campus_id: str | None = None,
    commit: bool = False,
) -> CourseDemandEvent | None:
    """Log one demand event. Returns ``None`` if there was no course to log.

    A blank or punctuation-only course code is a no-op rather than an error:
    this is called from a search box where "" is a perfectly normal query, and
    a demand signal is never worth failing a user's request over.
    """
    norm = normalize_course_code(course_code)
    if not norm:
        return None

    event = CourseDemandEvent(
        course_code_norm=norm,
        campus_id=campus_id,
        event_type=_text(event_type),
        created_at=utcnow(),
    )
    session.add(event)
    if commit:
        await session.commit()
    return event


async def record_many(
    session: AsyncSession,
    course_codes: Iterable[str | None],
    event_type: DemandEventType | str,
    *,
    campus_id: str | None = None,
    commit: bool = False,
) -> list[CourseDemandEvent]:
    """Log one event per distinct course code, in a single flush.

    Used by search, where a result page spans several courses. Duplicates are
    collapsed -- one query is one signal, however many hits it returned, or a
    popular course would out-trend a genuinely wanted one on volume alone.
    """
    seen: list[str] = []
    for raw in course_codes:
        norm = normalize_course_code(raw)
        if norm and norm not in seen:
            seen.append(norm)
    if not seen:
        return []

    now = utcnow()
    events = [
        CourseDemandEvent(
            course_code_norm=norm,
            campus_id=campus_id,
            event_type=_text(event_type),
            created_at=now,
        )
        for norm in seen
    ]
    session.add_all(events)
    if commit:
        await session.commit()
    return events


__all__ = ["record", "record_many"]
