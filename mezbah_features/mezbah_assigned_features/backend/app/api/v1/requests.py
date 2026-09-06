"""The request board (FR 2.1).

A ``Request`` is two things at once, and the second one is easy to miss: it is
the wanted-ad other students read *and* the forward-looking saved search the
auto-matcher runs against every newly approved listing (FR 2.3). Everything
odd-looking in this module follows from that.

Two consequences worth spelling out:

* **A saved search that ignores today's catalogue is a bad saved search.**
  ``services.matcher`` only fires on approval, so a request posted for a course
  that already has approved listings would sit silent until someone happened to
  upload another one. ``POST /requests`` therefore scans the existing catalogue
  once, in a ``BackgroundTask`` so the student's click still returns
  immediately.
* **Editing criteria reopens a matched request.** The MATCHED status means
  "this want was answered"; change what is wanted and that is no longer true,
  so the request goes back to OPEN and becomes matchable again. The same
  reasoning as a listing returning to PENDING when its metadata is edited.

MATCHED is never settable by hand -- it is the matcher's word for what
happened, and letting the owner assert it would leave a request advertising a
match that does not exist.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, status
from sqlalchemy import func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.db import SessionLocal
from app.core.deps import CurrentUser, SessionDep
from app.core.util import normalize_course_code, normalize_edition, utcnow
from app.models import (
    Campus,
    DemandEventType,
    ListingStatus,
    NotificationType,
    Request,
    RequestStatus,
    StudyMaterial,
    User,
)
from app.schemas import (
    MessageResponse,
    Page,
    RequestCreate,
    RequestRead,
    RequestUpdate,
)
from app.services import demand, notifier

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/requests", tags=["requests"])

#: How many already-approved listings the immediate scan will look at. The
#: notification names one of them and the board shows the rest, so pulling more
#: would buy nothing but a slower background task.
IMMEDIATE_MATCH_LIMIT = 5


# ---------------------------------------------------------------------------
# serialisation
# ---------------------------------------------------------------------------


def _to_read(
    request: Request, requester_name: str | None, campus_name: str | None
) -> RequestRead:
    """Row + the two joined display names.

    Names are joined rather than denormalised onto ``requests`` so a student
    who changes their name does not leave a stale byline on the board.
    """
    read = RequestRead.model_validate(request)
    read.requester_name = requester_name
    read.campus_name = campus_name
    return read


# ---------------------------------------------------------------------------
# the immediate scan -- see the module docstring
# ---------------------------------------------------------------------------


def _matches_edition(request_edition: str | None, listing_edition: str | None) -> bool:
    """Unspecified matches anything; otherwise compare normalised forms.

    Kept out of SQL for the same reason ``services.matcher`` keeps it out:
    ``3rd Ed.`` and ``3RDED`` are the same edition and SQLite has no expression
    that says so. The candidate set is one course code wide by this point.
    """
    if not request_edition:
        return True
    return normalize_edition(request_edition) == normalize_edition(listing_edition)


async def _find_existing_matches(
    session: AsyncSession, request: Request
) -> list[StudyMaterial]:
    """Approved listings that already satisfy ``request``, newest first.

    The criteria are the mirror image of ``matcher.match_listing``: a NULL on
    the request means "don't care" and matches everything, and only a priced
    listing is measured against ``max_price`` -- a free book is inside every
    budget.
    """
    criteria: list[Any] = [
        StudyMaterial.status == ListingStatus.APPROVED,
        StudyMaterial.course_code_norm == request.course_code_norm,
        # Your own upload answering your own request is not news.
        StudyMaterial.uploader_id != request.requester_id,
    ]
    if request.department:
        criteria.append(StudyMaterial.department == request.department)
    if request.listing_type:
        criteria.append(StudyMaterial.listing_type == request.listing_type)
    if request.campus_id:
        criteria.append(StudyMaterial.campus_id == request.campus_id)
    if request.max_price is not None:
        criteria.append(
            or_(
                StudyMaterial.price.is_(None),
                StudyMaterial.price <= request.max_price,
            )
        )

    stmt = (
        select(StudyMaterial)
        .where(*criteria)
        .order_by(StudyMaterial.upload_date.desc())
        # Over-fetch a little: the edition filter below runs in Python and may
        # discard some of these.
        .limit(IMMEDIATE_MATCH_LIMIT * 4)
    )
    candidates = (await session.execute(stmt)).scalars().all()
    matches = [c for c in candidates if _matches_edition(request.edition, c.edition)]
    return matches[:IMMEDIATE_MATCH_LIMIT]


async def scan_catalogue_for_request(request_id: str) -> int:
    """Notify the requester if the catalogue already answers their request.

    Runs in a ``BackgroundTask``, which means two things: it opens its own
    session (the request's is closed by the time this runs), and it must never
    raise -- an exception here would be logged by starlette long after the
    student's 201 was returned, so it is caught and logged deliberately.

    Returns the number of notifications sent, for tests and logging.
    """
    try:
        async with SessionLocal() as session:
            request = (
                await session.execute(
                    select(Request).where(Request.request_id == request_id)
                )
            ).scalar_one_or_none()
            # A request closed or matched between the POST and this task has
            # nothing to say.
            if request is None or request.status != RequestStatus.OPEN:
                return 0

            matches = await _find_existing_matches(session, request)
            if not matches:
                return 0

            best = matches[0]
            extra = len(matches) - 1
            message = (
                f"{len(matches)} listings for {request.course_code} are already "
                f"available, including '{best.title}'."
                if extra
                else f"'{best.title}' already matches your request for "
                f"{request.course_code}."
            )
            await notifier.notify(
                session,
                user_id=request.requester_id,
                type=NotificationType.REQUEST_MATCH,
                message=message,
                # Point at the listing, not the request: the useful next click
                # is opening the material, and this also lets the approval-time
                # matcher see that this pair has already been announced.
                ref_type="listing",
                ref_id=best.listing_id,
                commit=False,
            )
            request.status = RequestStatus.MATCHED
            session.add(request)
            await session.commit()
            return 1
    except Exception:  # noqa: BLE001 -- a background nicety must not crash the app
        logger.exception("immediate match scan failed for request %s", request_id)
        return 0


# ---------------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------------


@router.get("", response_model=Page[RequestRead], summary="Browse the request board")
async def list_requests(
    session: SessionDep,
    current_user: CurrentUser,
    status_filter: Annotated[RequestStatus | None, Query(alias="status")] = None,
    course_code: Annotated[str | None, Query(max_length=32)] = None,
    campus_id: str | None = None,
    mine: bool = False,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Page[RequestRead]:
    """Newest first.

    Without an explicit ``status`` the public board hides CLOSED requests --
    a withdrawn want-ad is noise to everyone but its owner, who sees the whole
    history through ``mine=true``.
    """
    criteria: list[Any] = []
    if mine:
        criteria.append(Request.requester_id == current_user.user_id)
    if status_filter is not None:
        criteria.append(Request.status == status_filter)
    elif not mine:
        criteria.append(Request.status != RequestStatus.CLOSED)
    if course_code:
        criteria.append(Request.course_code_norm == normalize_course_code(course_code))
    if campus_id:
        criteria.append(Request.campus_id == campus_id)

    total = int(
        (
            await session.execute(
                select(func.count()).select_from(Request).where(*criteria)
            )
        ).scalar_one()
    )

    stmt = (
        select(Request, User.name, Campus.name)
        .join(User, User.user_id == Request.requester_id)
        # LEFT JOIN: campus_id is NULL for an any-campus request.
        .outerjoin(Campus, Campus.campus_id == Request.campus_id)
        .where(*criteria)
        .order_by(Request.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = (await session.execute(stmt)).all()
    items = [_to_read(request, name, campus) for request, name, campus in rows]
    return Page.build(items, total, page, page_size)


@router.post(
    "",
    response_model=RequestRead,
    status_code=status.HTTP_201_CREATED,
    summary="Post a request",
)
async def create_request(
    payload: RequestCreate,
    session: SessionDep,
    current_user: CurrentUser,
    background: BackgroundTasks,
) -> RequestRead:
    """Create the wanted-ad, log the demand signal, then scan the catalogue.

    The demand event rides in the same transaction as the request: FR 4.4
    weights a posted request higher than any other signal (``DEMAND_WEIGHTS``),
    so losing it would understate exactly the demand that matters most.
    """
    campus_name: str | None = None
    if payload.campus_id:
        campus = (
            await session.execute(
                select(Campus).where(Campus.campus_id == payload.campus_id)
            )
        ).scalar_one_or_none()
        if campus is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="That campus does not exist. Leave it empty to match any campus.",
            )
        campus_name = campus.name

    request = Request(
        requester_id=current_user.user_id,
        course_code=payload.course_code,
        course_code_norm=payload.course_code_norm,
        department=payload.department,
        edition=payload.edition,
        listing_type=payload.listing_type.value if payload.listing_type else None,
        max_price=payload.max_price,
        campus_id=payload.campus_id,
        description=payload.description,
        status=RequestStatus.OPEN,
        created_at=utcnow(),
    )
    session.add(request)

    # An any-campus request is still demand *somewhere*; attribute it to the
    # requester's own campus so the trending dashboard is never blank for it.
    await demand.record(
        session,
        course_code=request.course_code,
        event_type=DemandEventType.REQUEST,
        campus_id=request.campus_id or current_user.campus_id,
    )
    await session.commit()
    await session.refresh(request)

    background.add_task(scan_catalogue_for_request, request.request_id)
    return _to_read(request, current_user.name, campus_name)


@router.patch(
    "/{request_id}", response_model=RequestRead, summary="Edit or close a request"
)
async def update_request(
    request_id: str,
    payload: RequestUpdate,
    session: SessionDep,
    current_user: CurrentUser,
) -> RequestRead:
    """Owner only. Set ``status`` to CLOSED to take it off the board."""
    request = await _get_request_or_404(session, request_id)
    _assert_owner(request, current_user)

    changes = payload.model_dump(exclude_unset=True)
    new_status = changes.pop("status", None)
    criteria_changed = bool(changes)

    if new_status == RequestStatus.MATCHED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "MATCHED is set by the auto-matcher when a listing answers this "
                "request. Close the request instead if you no longer need it."
            ),
        )

    # Enum columns are TEXT: store the value, never the member, or the CHECK
    # constraint sees something it has never heard of.
    if "listing_type" in changes:
        listing_type = changes.pop("listing_type")
        request.listing_type = listing_type.value if listing_type else None
    for field, value in changes.items():
        setattr(request, field, value)
    if "course_code" in changes:
        # course_code_norm is what the matcher, trending and duplicate checks
        # all key off; letting the two drift silently disables matching.
        request.course_code_norm = normalize_course_code(request.course_code)

    if new_status is not None:
        request.status = new_status.value
    elif criteria_changed and request.status == RequestStatus.MATCHED:
        # The old match answered the old criteria. See the module docstring.
        request.status = RequestStatus.OPEN

    session.add(request)
    await session.commit()
    await session.refresh(request)
    return _to_read(request, current_user.name, await _campus_name(session, request))


@router.delete(
    "/{request_id}", response_model=MessageResponse, summary="Delete a request"
)
async def delete_request(
    request_id: str, session: SessionDep, current_user: CurrentUser
) -> MessageResponse:
    """Owner only. Nothing references a request, so this is a real delete."""
    request = await _get_request_or_404(session, request_id)
    _assert_owner(request, current_user)

    await session.delete(request)
    await session.commit()
    return MessageResponse(detail="Request deleted.")


# ---------------------------------------------------------------------------
# shared lookups
# ---------------------------------------------------------------------------


async def _get_request_or_404(session: AsyncSession, request_id: str) -> Request:
    request = (
        await session.execute(select(Request).where(Request.request_id == request_id))
    ).scalar_one_or_none()
    if request is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That request no longer exists.",
        )
    return request


def _assert_owner(request: Request, user: User) -> None:
    if request.requester_id != user.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This request belongs to someone else.",
        )


async def _campus_name(session: AsyncSession, request: Request) -> str | None:
    if not request.campus_id:
        return None
    return (
        await session.execute(
            select(Campus.name).where(Campus.campus_id == request.campus_id)
        )
    ).scalars().first()


__all__ = ["router", "scan_catalogue_for_request"]
