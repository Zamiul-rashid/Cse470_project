"""The two Module 4 dashboards: leaderboard (FR 4.2) and trending (FR 4.4).

Authenticated but *not* admin-only. Both are read models over data every
student contributed to, and hiding the leaderboard from the students on it
would remove the only thing that makes it work as an incentive. The campus
filter is FR 4.3 reaching these endpoints: ``campus_id`` scopes both rankings
so a student on a small campus is not permanently buried under a larger one.

Neither handler contains a formula. Scoring, weighting, the rolling window and
the five-minute cache all live in ``services.analytics`` -- these functions
exist to attach query validation and a ``response_model`` to it, which is the
router's whole job in this codebase.
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.core.deps import CurrentUser, SessionDep
from app.schemas import LeaderboardEntry, TrendingCourse
from app.services import analytics

router = APIRouter(prefix="/analytics", tags=["analytics"])

#: Dashboards, not exports. The service clamps to the same ceiling; declaring
#: it here too turns an oversized request into a 422 that names the parameter
#: rather than a silently shortened list.
MAX_LIMIT = analytics.MAX_LIMIT


@router.get("/leaderboard", response_model=list[LeaderboardEntry])
async def leaderboard(
    session: SessionDep,
    _user: CurrentUser,
    campus_id: str | None = Query(
        None, description="Scope the ranking to one campus. Omit for all campuses."
    ),
    limit: int = Query(10, ge=1, le=MAX_LIMIT),
) -> list[LeaderboardEntry]:
    """Top contributors, ranked by the published score.

        score = 2*approved_uploads + 3*completed_transactions
                + 5*max(0, rating_avg - 3)

    ``rank`` is assigned server-side after sorting so ties resolve once, here,
    rather than differently in every client that re-sorts the list.
    """
    rows = await analytics.top_contributors(session, campus_id=campus_id, limit=limit)
    return [LeaderboardEntry.model_validate(row) for row in rows]


@router.get("/trending-courses", response_model=list[TrendingCourse])
async def trending_courses(
    session: SessionDep,
    _user: CurrentUser,
    campus_id: str | None = Query(
        None, description="Scope the demand window to one campus."
    ),
    limit: int = Query(10, ge=1, le=MAX_LIMIT),
) -> list[TrendingCourse]:
    """Weighted demand over the rolling window, with current supply beside it.

        demand_score = 3*requests + 2*wishlist_adds + 1*searches + 0.5*views

    ``available_listings`` is what makes the dashboard actionable rather than
    decorative: high demand against zero supply is exactly the moment a student
    should post a request (FR 2.1).
    """
    rows = await analytics.trending_courses(session, campus_id=campus_id, limit=limit)
    return [TrendingCourse.model_validate(row) for row in rows]


__all__ = ["MAX_LIMIT", "router"]
