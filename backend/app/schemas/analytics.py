"""Dashboard rows (FR 4.2, 4.4).

Both models carry their inputs alongside the derived number. The leaderboard
returns ``approved_uploads``, ``completed_transactions`` and ``rating_avg`` next
to ``score``; trending returns each event count next to ``demand_score``. A
ranking nobody can decompose reads as arbitrary, and the two formulas are
worth showing:

    score        = 2*approved_uploads + 3*completed_transactions
                   + 5*max(0, rating_avg - 3)
    demand_score = 3*requests + 2*wishlist_adds + 1*searches + 0.5*views

``rank`` is assigned by the service after sorting, so ties resolve once on the
server rather than differently in every client.
"""

from __future__ import annotations

from pydantic import Field

from app.schemas.common import ORMModel


class LeaderboardEntry(ORMModel):
    rank: int = Field(ge=1)
    user_id: str
    name: str
    campus_name: str | None = None
    approved_uploads: int = 0
    completed_transactions: int = 0
    rating_avg: float = 0.0
    score: float = 0.0


class TrendingCourse(ORMModel):
    """One course over the rolling trending window (30 days by default).

    ``available_listings`` is the payoff column: high demand with zero supply
    is the signal that should push a student to post a request.
    """

    rank: int = Field(ge=1)
    course_code: str
    department: str | None = None
    demand_score: float = 0.0
    request_count: int = 0
    wishlist_count: int = 0
    search_count: int = 0
    view_count: int = 0
    available_listings: int = 0


__all__ = ["LeaderboardEntry", "TrendingCourse"]
