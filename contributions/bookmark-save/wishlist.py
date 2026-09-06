"""Bookmarks (FR 2.4).

The wishlist is the *backward*-looking half of module 2: it saves listings that
already exist. It deliberately does not drive auto-match -- a bookmark can
never match a listing created tomorrow, which is why FR 2.3 keys off
``requests`` instead. What the wishlist does feed is
demand: a WISHLIST_ADD is the second-heaviest signal in ``DEMAND_WEIGHTS``, and
``services.matcher.match_wishlist_watchers`` uses the bookmarks as a soft
"interested in this course" list.

Two behaviours that look like leniency and are actually correctness:

* **Adding twice is not an error.** The bookmark button is a toggle rendered
  from a cached list; a double click, or two tabs, must not produce a 409 the
  user cannot act on. The second add is a no-op -- and, importantly, logs no
  second demand event, or spam-clicking a heart would out-trend a genuinely
  wanted course.
* **The wishlist row is created on demand.** Registration creates one, but a
  seeded or migrated account that somehow has none must still be able to
  bookmark rather than 500.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.deps import CurrentUser, SessionDep
from app.core.util import utcnow
from app.models import (
    PUBLIC_LISTING_STATUSES,
    Campus,
    DemandEventType,
    Review,
    StudyMaterial,
    User,
    Wishlist,
    WishlistItem,
)
from app.schemas import MaterialRead, MessageResponse, Page
from app.services import demand

router = APIRouter(prefix="/wishlist", tags=["wishlist"])


class WishlistItemCreate(BaseModel):
    """Body of ``POST /wishlist/items``.

    Local to this router: it is one field, and putting it in
    ``schemas.community`` would suggest something else reuses it.
    """

    listing_id: str


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def get_or_create_wishlist(session: AsyncSession, user_id: str) -> Wishlist:
    """One wishlist per user, looked up by ``user_id`` (which is UNIQUE)."""
    wishlist = (
        await session.execute(select(Wishlist).where(Wishlist.user_id == user_id))
    ).scalar_one_or_none()
    if wishlist is None:
        wishlist = Wishlist(user_id=user_id)
        session.add(wishlist)
        await session.flush()
    return wishlist


async def _get_listing_or_404(session: AsyncSession, listing_id: str) -> StudyMaterial:
    listing = (
        await session.execute(
            select(StudyMaterial).where(StudyMaterial.listing_id == listing_id)
        )
    ).scalar_one_or_none()
    if listing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That listing no longer exists.",
        )
    return listing


# ---------------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------------


@router.get(
    "/items", response_model=Page[MaterialRead], summary="List saved listings"
)
async def list_wishlist_items(
    session: SessionDep,
    current_user: CurrentUser,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Page[MaterialRead]:
    """Most recently saved first, as full listing cards.

    The rating columns come back as correlated sub-selects rather than a
    ``GROUP BY``: aggregating over the join would either need a grouping on
    every selected column or a second round trip, and this way one statement
    returns exactly the card the browse grid already knows how to render.
    """
    wishlist = await get_or_create_wishlist(session, current_user.user_id)

    total = int(
        (
            await session.execute(
                select(func.count())
                .select_from(WishlistItem)
                .where(WishlistItem.wishlist_id == wishlist.wishlist_id)
            )
        ).scalar_one()
    )

    review_count = (
        select(func.count(Review.review_id))
        .where(Review.listing_id == StudyMaterial.listing_id)
        .correlate(StudyMaterial)
        .scalar_subquery()
    )
    avg_rating = (
        select(func.avg(Review.rating))
        .where(Review.listing_id == StudyMaterial.listing_id)
        .correlate(StudyMaterial)
        .scalar_subquery()
    )

    stmt = (
        select(StudyMaterial, User.name, User.rating_avg, Campus.name, review_count, avg_rating)
        .join(WishlistItem, WishlistItem.listing_id == StudyMaterial.listing_id)
        .join(User, User.user_id == StudyMaterial.uploader_id)
        .join(Campus, Campus.campus_id == StudyMaterial.campus_id)
        .where(WishlistItem.wishlist_id == wishlist.wishlist_id)
        .order_by(WishlistItem.added_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = (await session.execute(stmt)).all()

    items: list[MaterialRead] = []
    for listing, uploader_name, uploader_rating, campus_name, reviews, rating in rows:
        read = MaterialRead.model_validate(listing)
        read.uploader_name = uploader_name
        read.uploader_rating = uploader_rating
        read.campus_name = campus_name
        # preview_path itself never leaves the server.
        read.has_preview = listing.preview_path is not None
        # Everything on this page is, by definition, bookmarked by the caller.
        read.is_bookmarked = True
        read.review_count = int(reviews or 0)
        read.avg_rating = round(float(rating), 2) if rating is not None else None
        items.append(read)

    return Page.build(items, total, page, page_size)


@router.post(
    "/items",
    response_model=MessageResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Save a listing",
)
async def add_wishlist_item(
    payload: WishlistItemCreate,
    session: SessionDep,
    current_user: CurrentUser,
) -> MessageResponse:
    """Idempotent. Logs a WISHLIST_ADD demand event on the first add only."""
    listing = await _get_listing_or_404(session, payload.listing_id)
    if listing.status not in PUBLIC_LISTING_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"That listing is not available to save (it is {listing.status}). "
                "Only approved listings can be bookmarked."
            ),
        )

    wishlist = await get_or_create_wishlist(session, current_user.user_id)
    existing = (
        await session.execute(
            select(WishlistItem).where(
                WishlistItem.wishlist_id == wishlist.wishlist_id,
                WishlistItem.listing_id == listing.listing_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        # Already saved is the outcome the caller asked for, so it is a success.
        return MessageResponse(detail="Already in your wishlist.")

    session.add(
        WishlistItem(
            wishlist_id=wishlist.wishlist_id,
            listing_id=listing.listing_id,
            added_at=utcnow(),
        )
    )
    # Demand is attributed to the listing's campus: that is where the shortage
    # is, regardless of which campus the interested student is registered on.
    await demand.record(
        session,
        course_code=listing.course_code,
        event_type=DemandEventType.WISHLIST_ADD,
        campus_id=listing.campus_id,
    )
    await session.commit()
    return MessageResponse(detail="Saved to your wishlist.")


@router.delete(
    "/items/{listing_id}",
    response_model=MessageResponse,
    summary="Remove a saved listing",
)
async def remove_wishlist_item(
    listing_id: str, session: SessionDep, current_user: CurrentUser
) -> MessageResponse:
    """404 only when the bookmark is missing -- the listing itself may be gone."""
    wishlist = await get_or_create_wishlist(session, current_user.user_id)
    item = (
        await session.execute(
            select(WishlistItem).where(
                WishlistItem.wishlist_id == wishlist.wishlist_id,
                WishlistItem.listing_id == listing_id,
            )
        )
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That listing is not in your wishlist.",
        )

    await session.delete(item)
    await session.commit()
    return MessageResponse(detail="Removed from your wishlist.")


__all__ = ["get_or_create_wishlist", "router"]
