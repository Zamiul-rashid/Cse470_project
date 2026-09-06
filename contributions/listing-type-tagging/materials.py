"""Listings: upload, discovery, detail, edit, takedown and file access.

FR 1.1-1.5, the campus half of FR 4.3, and the write side of FR 4.4. This is
the router the rest of the system reads from, so four things are settled here
rather than left to each endpoint:

* **One row shape.** Five endpoints return a ``MaterialRead`` and every one of
  them builds it through :func:`_material_read` over the join in
  :func:`_material_select`. Uploader name, campus name, review count, average
  rating and the caller's bookmark flag are columns of that one statement --
  a per-row lookup would turn a 24-card browse page into 96 queries.
* **Visibility is a filter, not an afterthought.** A non-owner sees only
  ``PUBLIC_LISTING_STATUSES``; the uploader sees their own PENDING and REJECTED
  rows; an admin sees everything. Applied as a WHERE clause so a hidden listing
  never reaches the serializer to be filtered out afterwards.
* **Keyword search goes through FTS5**, which means
  user text has to be turned into a MATCH expression. Raw input is a syntax
  error waiting to happen -- ``AND``, ``"`` and ``*`` are all operators -- so
  the query is stripped to tokens and requoted, and anything that survives
  that as nothing falls back to a LIKE rather than a 500.
* **Paths never leave the server.** ``file_path`` and ``preview_path`` are not
  on ``MaterialRead``; bytes are served by ``/preview`` and ``/file``, and the
  authorization on the second one is what makes the first one worth having.

Demand events and the auto-matcher run in ``BackgroundTasks``, each on its own
session: the request's session is closed by the time they run, and neither is
worth making a student wait for.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Annotated, Any, Final, Literal

from fastapi import (
    APIRouter,
    BackgroundTasks,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse
from pydantic import ValidationError
from sqlalchemy import (
    ColumnElement,
    Row,
    Select,
    false,
    func,
    literal,
    literal_column,
    or_,
    text,
)
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from app.core.config import settings
from app.core.db import SessionLocal
from app.core.deps import CurrentUser, OptionalUser, SessionDep
from app.core.util import normalize_course_code, utcnow
from app.models import (
    PUBLIC_LISTING_STATUSES,
    Campus,
    DemandEventType,
    ListingStatus,
    ListingType,
    Review,
    StudyMaterial,
    Transaction,
    TransactionStatus,
    User,
    UserRole,
    Wishlist,
    WishlistItem,
)
from app.schemas import (
    DuplicateCandidate,
    DuplicateCheckRequest,
    DuplicateCheckResponse,
    MaterialCreate,
    MaterialRead,
    MaterialUpdate,
    MessageResponse,
    Page,
    PriceSuggestion,
    ReviewRead,
)
from app.services import demand, matcher, recommendation, storage

logger = logging.getLogger(__name__)

router = APIRouter(tags=["materials"])

#: Everything an FTS5 MATCH expression may not contain. Quoting the tokens
#: afterwards is what makes ``AND``, ``NEAR`` and ``*`` in user text harmless.
_FTS_STRIP = re.compile(r"[^0-9A-Za-z ]+")

#: The MATCH is run as its own statement and its rowids are fed back into the
#: main query, so the hit list has to stay small enough to sit in an IN clause.
#: At assignment scale nobody pages past this; a listing beyond hit 500 for a
#: keyword is not findable by that keyword anyway.
_FTS_MAX_HITS: Final[int] = 500

#: A search box takes course codes and prose alike, and only the first is a
#: demand signal for a *course*. "CSE470" counts, "data structures" does not.
_COURSE_CODE_RE = re.compile(r"^[A-Z]{2,6}[0-9]{3,4}$")

#: Statuses that mean somebody is mid-deal on this listing. Deleting one out
#: from under a transaction leaves the buyer with a reserved item and no page.
_LIVE_TRANSACTION_STATUSES: Final[tuple[str, ...]] = (
    TransactionStatus.REQUESTED,
    TransactionStatus.ACCEPTED,
    TransactionStatus.AWAITING_HANDOFF,
)

#: Editing rewrites what a buyer agreed to, so it is refused once a listing is
#: spoken for or gone. PENDING/APPROVED/REJECTED are the editable states.
_EDITABLE_STATUSES: Final[tuple[str, ...]] = (
    ListingStatus.PENDING,
    ListingStatus.APPROVED,
    ListingStatus.REJECTED,
)

_MEDIA_TYPES: Final[dict[str, str]] = {
    ".pdf": storage.PDF_MIME,
    ".png": storage.PNG_MIME,
    ".jpg": storage.JPEG_MIME,
    ".jpeg": storage.JPEG_MIME,
}

MaterialSort = Literal["newest", "oldest", "price_asc", "price_desc", "rating"]

#: Review aggregate per listing. A LEFT JOIN against this is what keeps
#: ``review_count`` and ``avg_rating`` off the N+1 path.
_REVIEW_AGG = (
    select(
        col(Review.listing_id).label("listing_id"),
        func.count(col(Review.review_id)).label("review_count"),
        func.avg(col(Review.rating)).label("avg_rating"),
    )
    .group_by(col(Review.listing_id))
    .subquery("review_agg")
)


# ---------------------------------------------------------------------------
# the one row shape
# ---------------------------------------------------------------------------


def _bookmark_flag(viewer: User | None) -> Any:
    """Correlated "is this in my wishlist?" column, or a constant 0.

    A subquery rather than a join because the join key depends on the caller:
    an anonymous browse has no wishlist to join to, and duplicating the whole
    statement for that case is how the two copies drift apart.
    """
    if viewer is None:
        return literal(0).label("is_bookmarked")
    return (
        select(func.count())
        .select_from(WishlistItem)
        .join(Wishlist, col(Wishlist.wishlist_id) == col(WishlistItem.wishlist_id))
        .where(
            col(WishlistItem.listing_id) == col(StudyMaterial.listing_id),
            col(Wishlist.user_id) == viewer.user_id,
        )
        .correlate(StudyMaterial)
        .scalar_subquery()
        .label("is_bookmarked")
    )


def _material_select(viewer: User | None) -> Select[Any]:
    """The join every ``MaterialRead`` endpoint reads from.

    Uploader and campus are inner joins (both columns are NOT NULL foreign
    keys); the review aggregate is outer, since most listings have no reviews
    and an inner join would silently hide them.
    """
    return (
        select(
            StudyMaterial,
            col(User.name).label("uploader_name"),
            col(User.rating_avg).label("uploader_rating"),
            col(Campus.name).label("campus_name"),
            func.coalesce(_REVIEW_AGG.c.review_count, 0).label("review_count"),
            _REVIEW_AGG.c.avg_rating.label("avg_rating"),
            _bookmark_flag(viewer),
        )
        .join(User, col(User.user_id) == col(StudyMaterial.uploader_id))
        .join(Campus, col(Campus.campus_id) == col(StudyMaterial.campus_id))
        .outerjoin(
            _REVIEW_AGG, _REVIEW_AGG.c.listing_id == col(StudyMaterial.listing_id)
        )
    )


def _material_read(row: Row[Any]) -> MaterialRead:
    """Turn one joined row into the response shape.

    Every endpoint that returns a listing goes through here, so a new field on
    ``MaterialRead`` is filled in one place instead of five -- and the browse
    grid can never disagree with the detail page about what a listing is.
    """
    material: StudyMaterial = row[0]
    average = row.avg_rating
    return MaterialRead.model_validate(material).model_copy(
        update={
            "uploader_name": row.uploader_name,
            "uploader_rating": float(row.uploader_rating or 0.0),
            "campus_name": row.campus_name,
            # The path itself never ships; this is the only bit of it the
            # client needs to decide whether to offer the preview button.
            "has_preview": bool(material.preview_path),
            "is_bookmarked": bool(row.is_bookmarked),
            "review_count": int(row.review_count or 0),
            "avg_rating": round(float(average), 2) if average is not None else None,
        }
    )


async def _read_one(
    session: AsyncSession, listing_id: str, viewer: User | None
) -> MaterialRead:
    """Re-read a listing through the canonical join. 404 if it vanished."""
    row = (
        await session.execute(
            _material_select(viewer).where(col(StudyMaterial.listing_id) == listing_id)
        )
    ).first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That listing no longer exists.",
        )
    return _material_read(row)


# ---------------------------------------------------------------------------
# lookups, visibility & validation
# ---------------------------------------------------------------------------


async def _get_listing(session: AsyncSession, listing_id: str) -> StudyMaterial:
    listing = (
        await session.execute(
            select(StudyMaterial).where(col(StudyMaterial.listing_id) == listing_id)
        )
    ).scalar_one_or_none()
    if listing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That listing does not exist. It may have been removed.",
        )
    return listing


def _is_visible(listing: StudyMaterial, viewer: User | None) -> bool:
    """Who may see a listing at all.

    A PENDING or REJECTED listing is between its uploader and the moderators,
    so everyone else gets a 404 rather than a 403 -- a 403 would confirm the
    listing exists, which is itself information the caller has no claim to.
    """
    if listing.status in PUBLIC_LISTING_STATUSES:
        return True
    if viewer is None:
        return False
    return viewer.role == UserRole.ADMIN or viewer.user_id == listing.uploader_id


def _assert_visible(listing: StudyMaterial, viewer: User | None) -> None:
    if not _is_visible(listing, viewer):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That listing does not exist. It may have been removed.",
        )


def _assert_owner(listing: StudyMaterial, user: User, action: str) -> None:
    if user.user_id != listing.uploader_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"This listing is not yours, so you cannot {action} it.",
        )


def _validation_error(exc: ValidationError) -> HTTPException:
    """Multipart fields dodge FastAPI's body validation, so re-raise by hand.

    ``MaterialCreate`` is constructed from ``Form`` values rather than bound to
    the request body, which means its ValidationError would surface as a 500.
    Flattening it to a 422 keeps the field name in the message.
    """
    problems = "; ".join(
        f"{'.'.join(str(part) for part in error['loc']) or 'body'}: {error['msg']}"
        for error in exc.errors()
    )
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=problems
    )


# ---------------------------------------------------------------------------
# background work -- own session, never the request's
# ---------------------------------------------------------------------------


async def _record_demand(
    course_code: str, event_type: DemandEventType, campus_id: str | None
) -> None:
    """Append one demand event after the response has gone out (FR 4.4).

    The request's session is closed by the time a BackgroundTask runs, so this
    opens its own and commits it -- an uncommitted event is a discarded one.
    Failures are logged and swallowed: an analytics row is never worth turning
    a successful search into an error the user sees.
    """
    async with SessionLocal() as session:
        try:
            await demand.record(
                session,
                course_code=course_code,
                event_type=event_type,
                campus_id=campus_id,
                commit=True,
            )
        except Exception:
            logger.warning("demand event failed for %r", course_code, exc_info=True)


async def _run_matcher(listing_id: str) -> None:
    """Notify open requests for a listing that just became visible (FR 2.3).

    Only ever scheduled for an APPROVED listing; the matcher re-checks that
    anyway, because notifying about a PENDING listing sends people to a 404.
    """
    async with SessionLocal() as session:
        try:
            listing = (
                await session.execute(
                    select(StudyMaterial).where(
                        col(StudyMaterial.listing_id) == listing_id
                    )
                )
            ).scalar_one_or_none()
            if listing is None:
                return
            await matcher.run_for_listing(session, listing)
        except Exception:
            logger.warning("auto-match failed for listing %s", listing_id, exc_info=True)


def _demand_course_code(q: str | None, course_code: str | None) -> str | None:
    """Which string, if either, is a statement about a course.

    An explicit ``course_code`` filter always is. A free-text query only counts
    when it *looks* like a course code -- otherwise every search for "notes"
    would invent demand for a course called NOTES.
    """
    if course_code and course_code.strip():
        return course_code
    if q:
        candidate = normalize_course_code(q)
        if _COURSE_CODE_RE.match(candidate):
            return candidate
    return None


# ---------------------------------------------------------------------------
# keyword search (FR 1.2)
# ---------------------------------------------------------------------------


def _fts_expression(raw: str) -> str | None:
    """Build a safe FTS5 MATCH string, or ``None`` if nothing usable is left.

    Each token is quoted (so it is a literal, not an operator), suffixed with
    ``*`` for prefix matching -- "algo" should find "algorithms" while the user
    is still typing -- and ANDed, because a two-word query means both words.
    """
    tokens = _FTS_STRIP.sub(" ", raw).split()
    if not tokens:
        return None
    return " AND ".join(f'"{token}"*' for token in tokens)


def _like_condition(raw: str) -> ColumnElement[bool]:
    """The fallback when FTS has nothing to work with.

    Punctuation-only queries ("???") and a missing FTS index both land here.
    SQLite's LIKE is already case-insensitive for ASCII, so no LOWER() needed.
    """
    pattern = f"%{raw.strip()}%"
    return or_(
        col(StudyMaterial.title).like(pattern),
        col(StudyMaterial.course_code).like(pattern),
    )


async def _keyword_condition(session: AsyncSession, raw: str) -> ColumnElement[bool]:
    """Run the MATCH, then constrain the main query to the rowids it returned.

    Joining ``materials_fts`` into the main statement is not an option -- it is
    an external-content virtual table, and mixing it into a filtered, sorted,
    paginated query is where FTS5 plans fall apart. Two statements, one IN
    clause, and the filter/sort logic stays ordinary SQL.
    """
    expression = _fts_expression(raw)
    if expression is None:
        return _like_condition(raw)

    try:
        result = await session.execute(
            text(
                "SELECT rowid FROM materials_fts "
                "WHERE materials_fts MATCH :q LIMIT :limit"
            ),
            {"q": expression, "limit": _FTS_MAX_HITS},
        )
        rowids = [int(row[0]) for row in result]
    except SQLAlchemyError:
        # A failed statement poisons the transaction until it is rolled back,
        # and the caller still has a count and a page query to run.
        await session.rollback()
        logger.warning("FTS match failed for %r; falling back to LIKE", raw, exc_info=True)
        return _like_condition(raw)

    if not rowids:
        return false()
    return literal_column("study_materials.rowid").in_(rowids)


def _order_by(sort: MaterialSort) -> list[Any]:
    """Sort clauses, always closing on a unique column.

    Without the ``listing_id`` tiebreaker two listings with the same price can
    swap places between page 1 and page 2 and the user sees one of them twice.

    Price sorts lead with an IS NULL key rather than NULLS LAST: EXCHANGE and
    FREE listings carry no price, and burying them is more useful than SQLite's
    default of sorting NULL as the cheapest thing on the page.
    """
    priced_first = col(StudyMaterial.price).is_(None).asc()
    clauses: dict[str, list[Any]] = {
        "newest": [col(StudyMaterial.upload_date).desc()],
        "oldest": [col(StudyMaterial.upload_date).asc()],
        "price_asc": [priced_first, col(StudyMaterial.price).asc()],
        "price_desc": [priced_first, col(StudyMaterial.price).desc()],
        "rating": [
            func.coalesce(_REVIEW_AGG.c.avg_rating, 0).desc(),
            col(StudyMaterial.upload_date).desc(),
        ],
    }
    return [*clauses[sort], col(StudyMaterial.listing_id).asc()]


# ---------------------------------------------------------------------------
# FR 1.1 -- upload
# ---------------------------------------------------------------------------


@router.post(
    "/materials",
    response_model=MaterialRead,
    status_code=status.HTTP_201_CREATED,
    summary="Upload study material",
)
async def create_material(
    session: SessionDep,
    current_user: CurrentUser,
    background: BackgroundTasks,
    file: Annotated[UploadFile, File(description="PDF, PNG or JPEG, 25 MB max.")],
    title: Annotated[str, Form()],
    course_code: Annotated[str, Form()],
    department: Annotated[str, Form()],
    semester: Annotated[str, Form()],
    listing_type: Annotated[ListingType, Form()],
    description: Annotated[str | None, Form()] = None,
    edition: Annotated[str | None, Form()] = None,
    price: Annotated[float | None, Form()] = None,
) -> MaterialRead:
    """Create a listing from a multipart upload (FR 1.1, 1.3, 1.4).

    Metadata is validated *before* the stream is touched, so a listing with a
    price on a FREE item is a 422 rather than an orphaned 25 MB blob in
    ``storage/materials/`` that nothing will ever reference.

    The listing lands PENDING unless ``auto_approve_listings`` is set -- the
    Phase 1 flag that makes the catalogue demoable before the moderation panel
    exists. When it is auto-approved it is visible
    immediately, so the auto-matcher runs here instead of on an admin's
    approval click; it goes in a BackgroundTask so the uploader's request does
    not wait on a fan-out of notifications.
    """
    try:
        payload = MaterialCreate(
            title=title,
            description=description,
            course_code=course_code,
            department=department,
            semester=semester,
            edition=edition,
            listing_type=listing_type,
            price=price,
        )
    except ValidationError as exc:
        raise _validation_error(exc) from exc

    saved = await storage.save_upload(file)

    listing = StudyMaterial(
        uploader_id=current_user.user_id,
        # A listing belongs to the campus of whoever posted it; there is no
        # field for it because posting on someone else's campus is meaningless.
        campus_id=current_user.campus_id,
        title=payload.title,
        description=payload.description,
        course_code=payload.course_code,
        course_code_norm=payload.course_code_norm,
        department=payload.department,
        semester=payload.semester,
        edition=payload.edition,
        listing_type=payload.listing_type.value,
        price=payload.price,
        status=(
            ListingStatus.APPROVED
            if settings.auto_approve_listings
            else ListingStatus.PENDING
        ),
        file_path=saved.file_path,
        preview_path=saved.preview_path,
        page_count=saved.page_count,
        file_hash=saved.file_hash,
        upload_date=utcnow(),
    )
    session.add(listing)
    await session.commit()
    await session.refresh(listing)

    if listing.status == ListingStatus.APPROVED:
        background.add_task(_run_matcher, listing.listing_id)

    return await _read_one(session, listing.listing_id, current_user)


# ---------------------------------------------------------------------------
# FR 1.5 -- duplicate detection
# ---------------------------------------------------------------------------


@router.post(
    "/materials/check-duplicate",
    response_model=DuplicateCheckResponse,
    summary="Warn about possible duplicates before submitting",
)
async def check_duplicate(
    session: SessionDep,
    current_user: CurrentUser,
    payload: DuplicateCheckRequest,
) -> DuplicateCheckResponse:
    """Candidate duplicates for a listing that does not exist yet (FR 1.5).

    Advisory only, and deliberately so: two students selling the same textbook
    is the normal case, so this never blocks a submit. An empty list means "no
    warning", including when the similarity pass itself failed.

    Scoped to the caller's campus -- a duplicate three campuses away is not
    something the uploader can do anything about.
    """
    candidates = await recommendation.check_duplicate(
        session,
        title=payload.title,
        course_code=payload.course_code,
        edition=payload.edition,
        file_hash=payload.file_hash,
        campus_id=current_user.campus_id,
    )
    return DuplicateCheckResponse(
        candidates=[
            DuplicateCandidate(
                listing_id=candidate["listing_id"],
                title=candidate["title"],
                course_code=candidate["course_code"],
                edition=candidate["edition"],
                price=candidate["price"],
                # The service reports 0-100; the schema declares 0.0-1.0.
                similarity=candidate["similarity_ratio"],
                reason=candidate["reason"],
                uploader_name=candidate["uploader_name"],
            )
            for candidate in candidates
        ]
    )


# ---------------------------------------------------------------------------
# FR 3.1 -- price suggestion
#
# Declared before /materials/{listing_id}: FastAPI matches routes in order, so
# the parameterised route would otherwise swallow "price-suggestion" as an id.
# ---------------------------------------------------------------------------


@router.get(
    "/materials/price-suggestion",
    response_model=PriceSuggestion,
    summary="Suggest a price for a course's material",
)
async def price_suggestion(
    session: SessionDep,
    current_user: CurrentUser,
    course_code: Annotated[str, Query(min_length=2, max_length=32)],
    edition: Annotated[str | None, Query(max_length=40)] = None,
    listing_type: Annotated[ListingType, Query()] = ListingType.SELL,
    campus_scoped: Annotated[bool, Query()] = True,
) -> PriceSuggestion:
    """Median and interquartile range over comparable listings (FR 3.1).

    Always returns ``basis`` and ``sample_size``, including the
    ``insufficient_data`` case: "not enough similar listings yet" is a more
    useful answer than a median drawn from a sample of one.

    ``campus_scoped`` defaults to true because a price is a local fact; turn it
    off on a campus with a thin catalogue to widen the sample.
    """
    suggestion = await recommendation.suggest_price(
        session,
        course_code=course_code,
        edition=edition,
        listing_type=listing_type.value,
        campus_id=current_user.campus_id if campus_scoped else None,
    )
    return PriceSuggestion.model_validate(suggestion)


# ---------------------------------------------------------------------------
# FR 1.2 / 4.3 -- browse & search
# ---------------------------------------------------------------------------


@router.get(
    "/materials",
    response_model=Page[MaterialRead],
    summary="Search and filter listings",
)
async def search_materials(
    session: SessionDep,
    viewer: OptionalUser,
    background: BackgroundTasks,
    q: Annotated[str | None, Query(max_length=200, description="Keyword search.")] = None,
    course_code: Annotated[str | None, Query(max_length=32)] = None,
    department: Annotated[str | None, Query(max_length=100)] = None,
    semester: Annotated[str | None, Query(max_length=40)] = None,
    listing_type: Annotated[list[ListingType] | None, Query()] = None,
    campus_id: Annotated[str | None, Query()] = None,
    min_price: Annotated[float | None, Query(ge=0)] = None,
    max_price: Annotated[float | None, Query(ge=0)] = None,
    status_filter: Annotated[
        ListingStatus | None,
        Query(alias="status", description="Owner or admin only."),
    ] = None,
    uploader_id: Annotated[str | None, Query()] = None,
    bookmarked_only: Annotated[bool, Query()] = False,
    sort: Annotated[MaterialSort, Query()] = "newest",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Page[MaterialRead]:
    """The browse grid, the my-listings page and the wishlist, in one query.

    Visibility rules, in order: an admin sees every status, a caller asking for
    their own ``uploader_id`` sees every status of theirs, and everyone else
    sees only ``PUBLIC_LISTING_STATUSES``. The explicit ``status`` filter is
    refused with a 403 to anyone outside the first two cases, since it is the
    one parameter that could otherwise expose the moderation queue.

    ``q`` runs through FTS5 (FR 1.2); a query that sanitises to nothing falls
    back to a LIKE on title and course code instead of erroring. A search that
    names a course also appends a SEARCH demand event, in the background,
    because the trending dashboard (FR 4.4) is only as good as the events
    collected in the weeks before anyone opens it.
    """
    is_admin = viewer is not None and viewer.role == UserRole.ADMIN
    own_listings = (
        viewer is not None and uploader_id is not None and uploader_id == viewer.user_id
    )

    conditions: list[ColumnElement[bool]] = []
    if status_filter is not None:
        if not (is_admin or own_listings):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "Filtering by status is only available for your own listings. "
                    "Pass uploader_id set to your own user id, or drop the status filter."
                ),
            )
        conditions.append(col(StudyMaterial.status) == status_filter.value)
    elif not (is_admin or own_listings):
        conditions.append(col(StudyMaterial.status).in_(PUBLIC_LISTING_STATUSES))

    if q and q.strip():
        conditions.append(await _keyword_condition(session, q))
    if course_code and course_code.strip():
        # Stored and queried normalised, so "cse 470" finds CSE470 (FR 1.5).
        conditions.append(
            col(StudyMaterial.course_code_norm) == normalize_course_code(course_code)
        )
    if department and department.strip():
        conditions.append(col(StudyMaterial.department) == department.strip())
    if semester and semester.strip():
        conditions.append(col(StudyMaterial.semester) == semester.strip())
    if listing_type:
        conditions.append(
            col(StudyMaterial.listing_type).in_([t.value for t in listing_type])
        )
    if campus_id:
        conditions.append(col(StudyMaterial.campus_id) == campus_id)
    if uploader_id:
        conditions.append(col(StudyMaterial.uploader_id) == uploader_id)
    if min_price is not None:
        conditions.append(col(StudyMaterial.price) >= min_price)
    if max_price is not None:
        conditions.append(col(StudyMaterial.price) <= max_price)

    if bookmarked_only:
        if viewer is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Sign in to see your bookmarked listings.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        # An IN over a subquery rather than a join, so the count query below can
        # reuse the exact same condition list.
        conditions.append(
            col(StudyMaterial.listing_id).in_(
                select(col(WishlistItem.listing_id))
                .join(Wishlist, col(Wishlist.wishlist_id) == col(WishlistItem.wishlist_id))
                .where(col(Wishlist.user_id) == viewer.user_id)
            )
        )

    total = (
        await session.execute(
            select(func.count()).select_from(StudyMaterial).where(*conditions)
        )
    ).scalar_one()

    rows: list[Row[Any]] = []
    if total:
        stmt = (
            _material_select(viewer)
            .where(*conditions)
            .order_by(*_order_by(sort))
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        rows = list((await session.execute(stmt)).all())

    demand_code = _demand_course_code(q, course_code)
    if demand_code:
        background.add_task(
            _record_demand, demand_code, DemandEventType.SEARCH, campus_id
        )

    return Page.build(
        items=[_material_read(row) for row in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


# ---------------------------------------------------------------------------
# detail, edit, takedown
# ---------------------------------------------------------------------------


@router.get(
    "/materials/{listing_id}",
    response_model=MaterialRead,
    summary="Listing detail",
)
async def get_material(
    session: SessionDep,
    viewer: OptionalUser,
    background: BackgroundTasks,
    listing_id: str,
) -> MaterialRead:
    """One listing, plus a VIEW demand event (FR 1.2, 4.4).

    A listing the caller may not see is a 404, not a 403 -- see
    :func:`_is_visible` for why. The demand event is written in the background
    on its own session, so the detail page is never slowed down (or broken) by
    an analytics write.
    """
    listing = await _get_listing(session, listing_id)
    _assert_visible(listing, viewer)

    background.add_task(
        _record_demand,
        listing.course_code_norm,
        DemandEventType.VIEW,
        listing.campus_id,
    )
    return await _read_one(session, listing_id, viewer)


@router.patch(
    "/materials/{listing_id}",
    response_model=MaterialRead,
    summary="Edit a listing",
)
async def update_material(
    session: SessionDep,
    current_user: CurrentUser,
    listing_id: str,
    payload: MaterialUpdate,
) -> MaterialRead:
    """Owner-only metadata edit (FR 1.1).

    **Editing an APPROVED listing sends it back to PENDING.** Moderation
    approved a particular title, course and description; letting the uploader
    swap them afterwards would make approval meaningless, so the listing
    re-enters the queue. The exception is the ``auto_approve_listings`` mode
    from Phase 1, where nothing would ever approve it again.

    Refused once the listing is RESERVED, COMPLETED or REMOVED: those are
    someone else's deal or an admin's decision, not the uploader's to rewrite.

    The merged result is re-validated through ``MaterialCreate`` because a
    PATCH may change one half of the price/listing-type pair -- setting a
    listing to FREE without clearing its price has to fail here, not in SQLite.
    """
    listing = await _get_listing(session, listing_id)
    _assert_owner(listing, current_user, "edit")

    if listing.status not in _EDITABLE_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"This listing is {listing.status} and can no longer be edited. "
                "Cancel the transaction first, or upload a new listing."
            ),
        )

    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Nothing to update -- send at least one field.",
        )

    try:
        merged = MaterialCreate(
            title=changes.get("title", listing.title),
            description=changes.get("description", listing.description),
            course_code=changes.get("course_code", listing.course_code),
            department=changes.get("department", listing.department),
            semester=changes.get("semester", listing.semester),
            edition=changes.get("edition", listing.edition),
            listing_type=ListingType(changes.get("listing_type", listing.listing_type)),
            price=changes.get("price", listing.price),
        )
    except ValidationError as exc:
        raise _validation_error(exc) from exc

    listing.title = merged.title
    listing.description = merged.description
    listing.course_code = merged.course_code
    listing.course_code_norm = merged.course_code_norm
    listing.department = merged.department
    listing.semester = merged.semester
    listing.edition = merged.edition
    listing.listing_type = merged.listing_type.value
    listing.price = merged.price

    if listing.status == ListingStatus.APPROVED and not settings.auto_approve_listings:
        listing.status = ListingStatus.PENDING
        # Clear the old decision too: a stale "approved by X" on a listing
        # waiting in the queue reads as a bug to the next moderator.
        listing.moderated_by = None
        listing.moderated_at = None
        listing.moderation_note = None

    session.add(listing)
    await session.commit()
    return await _read_one(session, listing_id, current_user)


@router.delete(
    "/materials/{listing_id}",
    response_model=MessageResponse,
    summary="Take a listing down",
)
async def delete_material(
    session: SessionDep,
    current_user: CurrentUser,
    listing_id: str,
) -> MessageResponse:
    """Soft delete to REMOVED. Uploader or admin.

    Soft, not hard: reviews, transactions and the export all reference the
    listing, and a real DELETE would either violate those foreign keys or
    silently rewrite someone's history. The stored file stays on disk for the
    same reason -- a completed buyer keeps their download.

    Refused with a 409 while a transaction is live on the listing: pulling it
    then would leave the counterparty holding a deal with nothing behind it.
    """
    listing = await _get_listing(session, listing_id)
    if current_user.role != UserRole.ADMIN:
        _assert_owner(listing, current_user, "delete")

    if listing.status == ListingStatus.REMOVED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This listing has already been removed.",
        )

    live = (
        await session.execute(
            select(col(Transaction.transaction_id))
            .where(
                col(Transaction.listing_id) == listing_id,
                col(Transaction.status).in_(_LIVE_TRANSACTION_STATUSES),
            )
            .limit(1)
        )
    ).first()
    if live is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "There is an active transaction on this listing. Cancel it first, "
                "then take the listing down."
            ),
        )

    listing.status = ListingStatus.REMOVED
    session.add(listing)
    await session.commit()
    return MessageResponse(detail="Listing removed. It is no longer visible to anyone.")


# ---------------------------------------------------------------------------
# FR 1.4 -- files
# ---------------------------------------------------------------------------


def _file_response(
    relative_path: str, *, filename: str, inline: bool
) -> FileResponse:
    """Serve a stored blob, addressed by a database column and nothing else.

    ``storage.resolve`` refuses anything outside ``storage/`` -- ``storage/`` is
    never a static mount, so this and the preview route are the only two doors
    a file can leave through.
    """
    path: Path = storage.resolve(relative_path)
    if not path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That file is missing from storage. Ask the uploader to re-upload it.",
        )
    return FileResponse(
        path,
        media_type=_MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream"),
        filename=f"{filename}{path.suffix.lower()}",
        content_disposition_type="inline" if inline else "attachment",
    )


def _safe_filename(title: str) -> str:
    """A downloadable name from a listing title; never from a stored path."""
    cleaned = re.sub(r"[^0-9A-Za-z]+", "-", title).strip("-").lower()
    return cleaned[:60] or "notevault-material"


@router.get(
    "/materials/{listing_id}/preview",
    response_class=FileResponse,
    summary="Stream the 3-page preview",
)
async def get_preview(
    session: SessionDep,
    current_user: CurrentUser,
    listing_id: str,
) -> FileResponse:
    """The teaser copy, for any signed-in user (FR 1.4).

    This is a different file, not the same file with pages hidden: the slice
    was taken at upload time, so the rest of the document is never in the
    browser to be pulled out of the network tab.
    """
    listing = await _get_listing(session, listing_id)
    _assert_visible(listing, current_user)

    if not listing.preview_path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="This listing has no preview. Message the uploader for details.",
        )
    return _file_response(
        listing.preview_path,
        filename=f"{_safe_filename(listing.title)}-preview",
        inline=True,
    )


@router.get(
    "/materials/{listing_id}/file",
    response_class=FileResponse,
    summary="Download the full file",
)
async def get_file(
    session: SessionDep,
    current_user: CurrentUser,
    listing_id: str,
) -> FileResponse:
    """The full document. Uploader, admin, or a COMPLETED counterparty only.

    This check is the whole point of the preview being a separate file. Three
    ways in, and no fourth:

    * the uploader, who owns the material;
    * an admin, who has to be able to inspect what a report is about;
    * the buyer or seller of a **COMPLETED** transaction for this listing --
      COMPLETED specifically, because a REQUESTED or CANCELLED transaction is
      one click away from being a free download for anyone.

    Everyone else gets a 403 that says which of those they would need to be.
    """
    listing = await _get_listing(session, listing_id)

    allowed = (
        current_user.role == UserRole.ADMIN
        or current_user.user_id == listing.uploader_id
    )
    if not allowed:
        counterparty = (
            await session.execute(
                select(col(Transaction.transaction_id))
                .where(
                    col(Transaction.listing_id) == listing_id,
                    col(Transaction.status) == TransactionStatus.COMPLETED,
                    or_(
                        col(Transaction.buyer_id) == current_user.user_id,
                        col(Transaction.seller_id) == current_user.user_id,
                    ),
                )
                .limit(1)
            )
        ).first()
        allowed = counterparty is not None

    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "The full file is only available to the uploader and to the other "
                "party of a completed transaction for this listing. Preview the "
                "first pages, then request the item -- the download unlocks once "
                "the handoff is confirmed."
            ),
        )

    return _file_response(
        listing.file_path, filename=_safe_filename(listing.title), inline=False
    )


# ---------------------------------------------------------------------------
# FR 2.5 -- the read side of reviews
# ---------------------------------------------------------------------------


@router.get(
    "/materials/{listing_id}/reviews",
    response_model=Page[ReviewRead],
    summary="Reviews left on this listing",
)
async def list_material_reviews(
    session: SessionDep,
    viewer: OptionalUser,
    listing_id: str,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Page[ReviewRead]:
    """Newest first, with the reviewer's name joined in (FR 2.5).

    Reviews are only written against a COMPLETED transaction, so this needs no
    authorization beyond being able to see the listing at all -- and it is a
    single join rather than a name lookup per review.
    """
    listing = await _get_listing(session, listing_id)
    _assert_visible(listing, viewer)

    total = (
        await session.execute(
            select(func.count())
            .select_from(Review)
            .where(col(Review.listing_id) == listing_id)
        )
    ).scalar_one()

    rows: list[Row[Any]] = []
    if total:
        stmt = (
            select(Review, col(User.name).label("reviewer_name"))
            .join(User, col(User.user_id) == col(Review.reviewer_id))
            .where(col(Review.listing_id) == listing_id)
            .order_by(col(Review.created_at).desc(), col(Review.review_id).asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        rows = list((await session.execute(stmt)).all())

    items = [
        ReviewRead.model_validate(row[0]).model_copy(
            update={
                "reviewer_name": row.reviewer_name,
                "listing_title": listing.title,
            }
        )
        for row in rows
    ]
    return Page.build(items=items, total=total, page=page, page_size=page_size)


__all__ = ["router"]
