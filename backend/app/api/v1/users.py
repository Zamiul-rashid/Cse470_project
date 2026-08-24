"""Own profile, public profiles, and the reviews a user has received.

Three fields on a profile are not columns of the row being read:

* ``campus_name`` -- a join, because a client showing "Dhaka" cannot be asked to
  resolve a campus id first.
* ``upload_count`` -- counted over the same statuses the leaderboard scores on
  (``services.analytics.CONTRIBUTING_LISTING_STATUSES``), so a profile and the
  FR 4.2 dashboard never disagree about how much someone has contributed.
* ``rating_avg`` / ``rating_count`` -- aggregated from ``reviews`` rather than
  read from the denormalised columns on ``users``. Those columns exist as a
  cache for sorting; recomputing here means a missed recompute shows up as a
  stale sort order, never as a wrong number on someone's profile.

All three arrive as LEFT JOINed aggregate subqueries in one statement -- loading
the user and then counting is N+1, and the count query would be written twice.
Columns are selected individually rather than as the ``User`` entity so
``password_hash`` never leaves the database at all.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Path, Query, status
from sqlalchemy import Row, Subquery, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from sqlmodel import select

from app.core.deps import CurrentUser, SessionDep
from app.models import Campus, Review, StudyMaterial, User
from app.schemas import Page, ReviewRead, UserMe, UserPublic, UserUpdate
from app.services.analytics import CONTRIBUTING_LISTING_STATUSES

router = APIRouter(tags=["users"])

_USER_NOT_FOUND = "No such user."


def _uploads_subquery() -> Subquery:
    """Approved-and-beyond uploads per user. PENDING/REJECTED/REMOVED do not count."""
    return (
        select(
            StudyMaterial.uploader_id.label("user_id"),
            func.count().label("upload_count"),
        )
        .where(StudyMaterial.status.in_(CONTRIBUTING_LISTING_STATUSES))
        .group_by(StudyMaterial.uploader_id)
        .subquery()
    )


def _ratings_subquery() -> Subquery:
    """Rating aggregate per *reviewee* -- reviews are received, not written."""
    return (
        select(
            Review.reviewee_id.label("user_id"),
            func.count().label("rating_count"),
            func.avg(Review.rating).label("rating_avg"),
        )
        .group_by(Review.reviewee_id)
        .subquery()
    )


async def _load_profile(session: AsyncSession, user_id: str) -> Row[Any]:
    """The one query behind every profile response. 404 if there is no such user."""
    uploads = _uploads_subquery()
    ratings = _ratings_subquery()

    stmt = (
        select(
            User.user_id,
            User.name,
            User.email,
            User.role,
            User.campus_id,
            User.created_at,
            Campus.name.label("campus_name"),
            # COALESCE, and outer joins throughout: a user who has uploaded
            # nothing and been reviewed by nobody is a new account, not a
            # missing row, and must still render.
            func.coalesce(uploads.c.upload_count, 0).label("upload_count"),
            func.coalesce(ratings.c.rating_count, 0).label("rating_count"),
            func.coalesce(ratings.c.rating_avg, 0.0).label("rating_avg"),
        )
        .select_from(User)
        .join(Campus, Campus.campus_id == User.campus_id, isouter=True)
        .join(uploads, uploads.c.user_id == User.user_id, isouter=True)
        .join(ratings, ratings.c.user_id == User.user_id, isouter=True)
        .where(User.user_id == user_id)
    )

    row = (await session.execute(stmt)).first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=_USER_NOT_FOUND
        )
    return row


def _public_fields(row: Row[Any]) -> dict[str, Any]:
    return {
        "user_id": row.user_id,
        "name": row.name,
        "campus_id": row.campus_id,
        "campus_name": row.campus_name,
        # Two decimals: the UI renders stars, and 4.333333 in a tooltip reads
        # like a bug rather than an average.
        "rating_avg": round(float(row.rating_avg or 0.0), 2),
        "rating_count": int(row.rating_count or 0),
        "upload_count": int(row.upload_count or 0),
        "role": row.role,
    }


# ``/users/me`` is declared before ``/users/{user_id}``: FastAPI matches in
# declaration order, and the other way round "me" is read as a user id.


@router.get("/users/me", response_model=UserMe, summary="Read your own profile")
async def read_me(session: SessionDep, current_user: CurrentUser) -> UserMe:
    row = await _load_profile(session, current_user.user_id)
    return UserMe(**_public_fields(row), email=row.email, created_at=row.created_at)


@router.patch("/users/me", response_model=UserMe, summary="Update your own profile")
async def update_me(
    payload: UserUpdate, session: SessionDep, current_user: CurrentUser
) -> UserMe:
    # exclude_unset, not exclude_none: PATCH means "omitted stays as it is",
    # and neither field is nullable, so an explicit null is still a no-op.
    updates = payload.model_dump(exclude_unset=True)

    if "campus_id" in updates and updates["campus_id"] is not None:
        campus = (
            await session.execute(
                select(Campus).where(Campus.campus_id == updates["campus_id"])
            )
        ).scalar_one_or_none()
        if campus is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="That campus does not exist. Pick one from the list.",
            )

    changed = False
    for field in ("name", "campus_id"):
        value = updates.get(field)
        if value is not None and value != getattr(current_user, field):
            setattr(current_user, field, value)
            changed = True

    if changed:
        session.add(current_user)
        await session.commit()

    row = await _load_profile(session, current_user.user_id)
    return UserMe(**_public_fields(row), email=row.email, created_at=row.created_at)


@router.get(
    "/users/{user_id}",
    response_model=UserPublic,
    summary="Read another user's public profile",
)
async def read_user(
    user_id: Annotated[str, Path(description="Id of the user to read")],
    session: SessionDep,
    current_user: CurrentUser,
) -> UserPublic:
    row = await _load_profile(session, user_id)
    # Deliberately narrower than UserMe: email and join date are the account
    # owner's business, and a counterparty only needs trust signals.
    return UserPublic(**_public_fields(row))


@router.get(
    "/users/{user_id}/reviews",
    response_model=Page[ReviewRead],
    summary="List the reviews a user has received",
)
async def list_user_reviews(
    user_id: Annotated[str, Path(description="Id of the reviewed user")],
    session: SessionDep,
    current_user: CurrentUser,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Page[ReviewRead]:
    exists = (
        await session.execute(select(User.user_id).where(User.user_id == user_id))
    ).scalar_one_or_none()
    if exists is None:
        # An empty page for an unknown id would read as "this user has no
        # reviews yet", which is a different and misleading answer.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=_USER_NOT_FOUND
        )

    total = int(
        (
            await session.execute(
                select(func.count())
                .select_from(Review)
                .where(Review.reviewee_id == user_id)
            )
        ).scalar_one()
    )

    # Aliased because the reviewer is a second row of the same table the
    # reviewee came from; the raw table name would be ambiguous.
    reviewer = aliased(User)
    stmt = (
        select(
            Review,
            reviewer.name.label("reviewer_name"),
            StudyMaterial.title.label("listing_title"),
        )
        .select_from(Review)
        .join(reviewer, reviewer.user_id == Review.reviewer_id, isouter=True)
        .join(
            StudyMaterial,
            StudyMaterial.listing_id == Review.listing_id,
            isouter=True,
        )
        .where(Review.reviewee_id == user_id)
        .order_by(Review.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )

    items = [
        ReviewRead(
            review_id=row.Review.review_id,
            rating=row.Review.rating,
            comment=row.Review.comment,
            created_at=row.Review.created_at,
            reviewer_id=row.Review.reviewer_id,
            reviewer_name=row.reviewer_name,
            reviewee_id=row.Review.reviewee_id,
            listing_id=row.Review.listing_id,
            listing_title=row.listing_title,
        )
        for row in (await session.execute(stmt)).all()
    ]
    return Page[ReviewRead].build(
        items=items, total=total, page=page, page_size=page_size
    )


__all__ = ["router"]
