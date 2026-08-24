"""Campus reference data (FR 4.3).

Read-only and unauthenticated. Campuses are seeded, never created through the
API, so there is one route and no write side. It has to be open because the
sign-up form needs the list before an account -- and therefore a token --
exists; the same list then populates the search filter and the analytics scope
selector, which is why it is not tucked inside the auth router.
"""

from __future__ import annotations

from fastapi import APIRouter
from sqlmodel import select

from app.core.deps import SessionDep
from app.models import Campus
from app.schemas import CampusRead

router = APIRouter(tags=["campuses"])


@router.get(
    "/campuses",
    response_model=list[CampusRead],
    summary="List all campuses",
)
async def list_campuses(session: SessionDep) -> list[Campus]:
    # Unpaged by name: this is a dropdown over a handful of rows, and a client
    # that has to page through a <select> is a client that renders it wrong.
    stmt = select(Campus).order_by(Campus.name.asc())
    return list((await session.execute(stmt)).scalars().all())


__all__ = ["router"]
