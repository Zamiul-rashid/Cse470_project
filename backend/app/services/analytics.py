"""Leaderboard and trending courses (FR 4.2, FR 4.4).

The ``AnalyticsService`` on the class diagram has no table of its own -- it is
stateless behaviour over data the other modules have been accumulating since
Phase 1. Two rules keep analytics from scanning the world on every request:

* **Aggregate in SQL, never in Python.** The obvious leaderboard -- load every
  user, then count their uploads and transactions in a loop -- is N+1 queries
  that get slower with every registration. Both dashboards here are one
  grouped query plus, for trending, one lookup of the courses that won.
* **Cache in-process for ``analytics_cache_seconds``.** A plain dict keyed by
  the query parameters. At assignment scale a materialised table is complexity
  nobody grades; five-minute-stale ranks are indistinguishable from live ones.

The cache is per-process, like the WebSocket registry, and is another reason
the app runs with ``--workers 1``. :meth:`AnalyticsService.invalidate` exists
so tests can seed data and read it back without waiting out the TTL.

Both formulas are published in the response models so a ranking is never a
number nobody can decompose:

    score        = 2*approved_uploads + 3*completed_transactions
                   + 5*max(0, rating_avg - 3)
    demand_score = 3*requests + 2*wishlist_adds + 1*searches + 0.5*views
"""

from __future__ import annotations

import time
from datetime import timedelta
from typing import Any

from sqlalchemy import case, func, union_all
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.config import settings
from app.core.util import utcnow
from app.models import (
    DEMAND_WEIGHTS,
    PUBLIC_LISTING_STATUSES,
    Campus,
    CourseDemandEvent,
    DemandEventType,
    ListingStatus,
    StudyMaterial,
    Transaction,
    TransactionStatus,
    User,
)

#: An upload only counts once a moderator has cleared it, and it keeps counting
#: after it sells -- RESERVED and COMPLETED are both "approved, then used".
#: PENDING/REJECTED/REMOVED would let spam inflate a rank.
CONTRIBUTING_LISTING_STATUSES: tuple[str, ...] = (
    ListingStatus.APPROVED,
    ListingStatus.RESERVED,
    ListingStatus.COMPLETED,
)

#: Ratings below this add nothing; above it, each star is worth 5 points. The
#: clamp is what stops one 5-star review outranking sustained contribution.
RATING_BASELINE = 3.0

#: Defensive ceiling: these are dashboard endpoints, not an export.
MAX_LIMIT = 100


class AnalyticsService:
    """Cached read models for the two Module 4 dashboards."""

    def __init__(self) -> None:
        # key -> (monotonic deadline, value). monotonic() rather than wall
        # clock so an NTP step or a DST change cannot freeze the cache.
        self._cache: dict[str, tuple[float, Any]] = {}

    # -- cache -------------------------------------------------------------

    def _get(self, key: str) -> Any | None:
        entry = self._cache.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if expires_at <= time.monotonic():
            self._cache.pop(key, None)
            return None
        return value

    def _put(self, key: str, value: Any) -> None:
        self._cache[key] = (time.monotonic() + settings.analytics_cache_seconds, value)

    def invalidate(self, key_prefix: str | None = None) -> None:
        """Drop cached results. Tests call this after seeding rows."""
        if key_prefix is None:
            self._cache.clear()
            return
        for key in [k for k in self._cache if k.startswith(key_prefix)]:
            self._cache.pop(key, None)

    # -- FR 4.2 ------------------------------------------------------------

    async def top_contributors(
        self,
        session: AsyncSession,
        *,
        campus_id: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Ranked contributors, shaped for ``schemas.analytics.LeaderboardEntry``.

        One query: two aggregate subqueries LEFT JOINed onto ``users``, so a
        user with no uploads and no transactions still appears (with zero)
        rather than vanishing from an inner join.
        """
        limit = max(1, min(limit, MAX_LIMIT))
        cache_key = f"leaderboard:{campus_id or '*'}:{limit}"
        cached = self._get(cache_key)
        if cached is not None:
            return [dict(row) for row in cached]

        uploads_sq = (
            select(
                StudyMaterial.uploader_id.label("user_id"),
                func.count().label("uploads"),
            )
            .where(StudyMaterial.status.in_(CONTRIBUTING_LISTING_STATUSES))
            .group_by(StudyMaterial.uploader_id)
            .subquery()
        )

        # A completed transaction credits *both* sides -- the seller supplied
        # the material, the buyer saw the exchange through. UNION ALL rather
        # than two joins so a user is counted once per row on each side.
        buyer_side = select(Transaction.buyer_id.label("user_id")).where(
            Transaction.status == TransactionStatus.COMPLETED
        )
        seller_side = select(Transaction.seller_id.label("user_id")).where(
            Transaction.status == TransactionStatus.COMPLETED
        )
        parties = union_all(buyer_side, seller_side).subquery()
        tx_sq = (
            select(parties.c.user_id, func.count().label("completed"))
            .group_by(parties.c.user_id)
            .subquery()
        )

        uploads = func.coalesce(uploads_sq.c.uploads, 0)
        completed = func.coalesce(tx_sq.c.completed, 0)
        # CASE rather than SQLite's two-argument max(): unambiguous to
        # SQLAlchemy, which would otherwise read func.max as the aggregate.
        rating_bonus = case(
            (User.rating_avg > RATING_BASELINE, User.rating_avg - RATING_BASELINE),
            else_=0.0,
        )
        score = (2.0 * uploads + 3.0 * completed + 5.0 * rating_bonus).label("score")

        stmt = (
            select(
                User.user_id,
                User.name,
                Campus.name.label("campus_name"),
                uploads.label("approved_uploads"),
                completed.label("completed_transactions"),
                User.rating_avg,
                score,
            )
            .select_from(User)
            .join(Campus, Campus.campus_id == User.campus_id, isouter=True)
            .join(uploads_sq, uploads_sq.c.user_id == User.user_id, isouter=True)
            .join(tx_sq, tx_sq.c.user_id == User.user_id, isouter=True)
            # Ties break on rating then name so two identical scores rank the
            # same way on every reload.
            .order_by(score.desc(), User.rating_avg.desc(), User.name.asc())
            .limit(limit)
        )
        if campus_id:
            stmt = stmt.where(User.campus_id == campus_id)

        rows = (await session.execute(stmt)).all()
        entries = [
            {
                "rank": index,
                "user_id": row.user_id,
                "name": row.name,
                "campus_name": row.campus_name,
                "approved_uploads": int(row.approved_uploads or 0),
                "completed_transactions": int(row.completed_transactions or 0),
                "rating_avg": round(float(row.rating_avg or 0.0), 2),
                "score": round(float(row.score or 0.0), 2),
            }
            for index, row in enumerate(rows, start=1)
        ]

        self._put(cache_key, entries)
        return [dict(entry) for entry in entries]

    # -- FR 4.4 ------------------------------------------------------------

    async def trending_courses(
        self,
        session: AsyncSession,
        *,
        campus_id: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Weighted demand over the rolling window, plus current supply.

        Shaped for ``schemas.analytics.TrendingCourse``. ``available_listings``
        is the column that makes the dashboard actionable: high demand against
        zero supply is exactly when a student should post a request.
        """
        limit = max(1, min(limit, MAX_LIMIT))
        cache_key = f"trending:{campus_id or '*'}:{limit}"
        cached = self._get(cache_key)
        if cached is not None:
            return [dict(row) for row in cached]

        since = utcnow() - timedelta(days=settings.trending_window_days)

        def _count(event_type: DemandEventType) -> Any:
            """Conditional count of one event type within the GROUP BY."""
            return func.coalesce(
                func.sum(case((CourseDemandEvent.event_type == event_type, 1), else_=0)),
                0,
            )

        counts = {event_type: _count(event_type) for event_type in DemandEventType}

        # Weighted sum built by folding the weight table, so DEMAND_WEIGHTS
        # stays the single definition of "how much does a signal count".
        demand_score: Any = 0.0
        for event_type, weight in DEMAND_WEIGHTS.items():
            demand_score = demand_score + weight * counts[event_type]
        demand_score = demand_score.label("demand_score")

        stmt = (
            select(
                CourseDemandEvent.course_code_norm,
                counts[DemandEventType.REQUEST].label("request_count"),
                counts[DemandEventType.WISHLIST_ADD].label("wishlist_count"),
                counts[DemandEventType.SEARCH].label("search_count"),
                counts[DemandEventType.VIEW].label("view_count"),
                demand_score,
            )
            .where(CourseDemandEvent.created_at >= since)
            .group_by(CourseDemandEvent.course_code_norm)
            .order_by(demand_score.desc(), CourseDemandEvent.course_code_norm.asc())
            .limit(limit)
        )
        if campus_id:
            stmt = stmt.where(CourseDemandEvent.campus_id == campus_id)

        rows = (await session.execute(stmt)).all()
        if not rows:
            self._put(cache_key, [])
            return []

        supply = await self._course_supply(
            session,
            [row.course_code_norm for row in rows],
            campus_id=campus_id,
        )

        courses = []
        for index, row in enumerate(rows, start=1):
            norm = row.course_code_norm
            listing_info = supply.get(norm, {})
            courses.append(
                {
                    "rank": index,
                    # Fall back to the normalised form: a course can trend on
                    # demand alone, with no listing to borrow a display code
                    # from. That is the interesting case, not an error.
                    "course_code": listing_info.get("course_code") or norm,
                    "department": listing_info.get("department"),
                    "demand_score": round(float(row.demand_score or 0.0), 2),
                    "request_count": int(row.request_count or 0),
                    "wishlist_count": int(row.wishlist_count or 0),
                    "search_count": int(row.search_count or 0),
                    "view_count": int(row.view_count or 0),
                    "available_listings": int(listing_info.get("available", 0)),
                }
            )

        self._put(cache_key, courses)
        return [dict(course) for course in courses]

    async def _course_supply(
        self,
        session: AsyncSession,
        course_codes: list[str],
        *,
        campus_id: str | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Display code, department and live listing count for the winning courses.

        Demand events store only ``course_code_norm``, which is not what a
        human wants to read, and they carry no department at all -- both come
        from whatever listings exist for the course. The availability count is
        restricted to publicly visible statuses; the representative code and
        department are not, so a course whose only listing is still PENDING
        still renders with a real name.
        """
        if not course_codes:
            return {}

        available = func.sum(
            case((StudyMaterial.status.in_(PUBLIC_LISTING_STATUSES), 1), else_=0)
        )
        stmt = (
            select(
                StudyMaterial.course_code_norm,
                func.min(StudyMaterial.course_code).label("course_code"),
                func.min(StudyMaterial.department).label("department"),
                available.label("available"),
            )
            .where(StudyMaterial.course_code_norm.in_(course_codes))
            .group_by(StudyMaterial.course_code_norm)
        )
        if campus_id:
            stmt = stmt.where(StudyMaterial.campus_id == campus_id)

        return {
            row.course_code_norm: {
                "course_code": row.course_code,
                "department": row.department,
                "available": int(row.available or 0),
            }
            for row in (await session.execute(stmt)).all()
        }


#: Process-wide singleton -- a second instance would mean a second, colder
#: cache, and two endpoints disagreeing about the same ranking.
analytics = AnalyticsService()


async def top_contributors(
    session: AsyncSession, *, campus_id: str | None = None, limit: int = 10
) -> list[dict[str, Any]]:
    """Module-level shorthand so routers never construct the service."""
    return await analytics.top_contributors(session, campus_id=campus_id, limit=limit)


async def trending_courses(
    session: AsyncSession, *, campus_id: str | None = None, limit: int = 10
) -> list[dict[str, Any]]:
    """Module-level shorthand so routers never construct the service."""
    return await analytics.trending_courses(session, campus_id=campus_id, limit=limit)


__all__ = [
    "CONTRIBUTING_LISTING_STATUSES",
    "MAX_LIMIT",
    "RATING_BASELINE",
    "AnalyticsService",
    "analytics",
    "top_contributors",
    "trending_courses",
]
