"""The RecommendationEngine: duplicate detection (FR 1.5) and price suggestion (FR 3.1).

The class on the reference diagram holds no state, so it lives here as async
functions over an ``AsyncSession`` instead of as a table.

Both halves are built to be honest rather than confident:

* ``check_duplicate`` **warns, never blocks.** Two students legitimately
  selling the same textbook is the normal case, so every candidate carries a
  human-readable reason the uploader can judge for themselves, and the whole
  function swallows its own failures -- a broken similarity pass must not cost
  someone their upload.
* ``suggest_price`` walks a fallback ladder and always reports which rung
  produced the number and how many listings it saw.
  The failure mode worth engineering against is a median presented from a
  sample of one; "not enough similar listings yet" is the more useful answer.

Two notes for callers on the dict shapes. ``similarity`` is the 0-100
rapidfuzz score that ``frontend/src/lib/types.ts`` documents, and
``similarity_ratio`` carries the same number on the 0.0-1.0 scale that
``schemas.material.DuplicateCandidate`` declares; likewise the price dict
carries ``suggested`` and its ``suggested_price`` alias. Emitting both spellings
is a line each and saves a translation layer between the two type definitions.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import timedelta
from math import ceil, floor
from typing import Any, Final

from rapidfuzz import fuzz
from sqlalchemy import ColumnElement, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from app.core.config import settings
from app.core.util import normalize_course_code, normalize_edition, utcnow
from app.models import (
    PRICED_LISTING_TYPES,
    PUBLIC_LISTING_STATUSES,
    ListingStatus,
    ListingType,
    StudyMaterial,
    User,
)

logger = logging.getLogger(__name__)

#: PENDING is deliberately in scope even though it is not publicly visible: an
#: uploader posting the same file twice in five minutes should hear about it
#: before a moderator has to notice two identical rows in the queue.
_CANDIDATE_STATUSES: Final[tuple[str, ...]] = (
    *PUBLIC_LISTING_STATUSES,
    ListingStatus.PENDING,
)

#: ``token_set_ratio`` runs in Python, so the fuzzy pass is bounded to the most
#: recent rows for the course rather than the whole table.
_SCAN_LIMIT: Final[int] = 200
_MAX_CANDIDATES: Final[int] = 5

#: What counts as evidence of a going rate. An APPROVED asking price and a
#: COMPLETED sale price are both real signals; REJECTED and REMOVED are not.
_PRICE_STATUSES: Final[tuple[str, ...]] = (
    ListingStatus.APPROVED,
    ListingStatus.RESERVED,
    ListingStatus.COMPLETED,
)

BASIS_COURSE_EDITION: Final[str] = "course_edition"
BASIS_COURSE: Final[str] = "course"
BASIS_DEPARTMENT: Final[str] = "department"
BASIS_CAMPUS: Final[str] = "campus"
BASIS_NONE: Final[str] = "insufficient_data"

CURRENCY: Final[str] = "BDT"


# ---------------------------------------------------------------------------
# FR 1.5 -- duplicate detection
# ---------------------------------------------------------------------------


async def check_duplicate(
    session: AsyncSession,
    *,
    title: str,
    course_code: str,
    edition: str | None = None,
    file_hash: str | None = None,
    campus_id: str | None = None,
    exclude_listing_id: str | None = None,
) -> list[dict[str, Any]]:
    """Up to five suspected duplicates, strongest first. Never raises.

    Three rules in descending confidence: an identical SHA-256 is a near
    certainty, the same course *and* edition with a near-identical title is
    strong, and the same course with a near-identical title is worth a look.
    ``file_hash`` is optional because the form asks as the user types, before
    the browser has read the file.

    Callers -- both ``POST /materials/check-duplicate`` and the ``warnings``
    array on create -- treat the result as advisory. An empty list means "no
    warning", including when this failed.
    """
    try:
        course_norm = normalize_course_code(course_code)
        edition_norm = normalize_edition(edition)
        clean_title = (title or "").strip()

        # An identical file is worth surfacing wherever it sits, so the hash
        # clause is ORed in rather than filtered by course code.
        scope: ColumnElement[bool] = col(StudyMaterial.course_code_norm) == course_norm
        if file_hash:
            scope = or_(scope, col(StudyMaterial.file_hash) == file_hash)

        conditions: list[ColumnElement[bool]] = [
            scope,
            col(StudyMaterial.status).in_(_CANDIDATE_STATUSES),
        ]
        if campus_id:
            # A duplicate on another campus is not something the uploader can
            # act on, so scoping keeps the warning actionable when the caller
            # knows which campus it is asking about.
            conditions.append(col(StudyMaterial.campus_id) == campus_id)
        if exclude_listing_id:
            # Set on edit, so a listing never reports itself as its own twin.
            conditions.append(col(StudyMaterial.listing_id) != exclude_listing_id)

        stmt = (
            select(StudyMaterial, User.name)
            .join(User, col(User.user_id) == col(StudyMaterial.uploader_id))
            .where(*conditions)
            .order_by(col(StudyMaterial.upload_date).desc())
            .limit(_SCAN_LIMIT)
        )
        rows = (await session.execute(stmt)).all()

        candidates: list[dict[str, Any]] = []
        for material, uploader_name in rows:
            scored = _score(
                material,
                title=clean_title,
                course_norm=course_norm,
                edition_norm=edition_norm,
                file_hash=file_hash,
            )
            if scored is None:
                continue
            similarity, reason, match_kind = scored
            candidates.append(
                {
                    "listing_id": material.listing_id,
                    "title": material.title,
                    "course_code": material.course_code,
                    "edition": material.edition,
                    "listing_type": material.listing_type,
                    "price": material.price,
                    "uploader_name": uploader_name,
                    "upload_date": material.upload_date,
                    "similarity": similarity,
                    "similarity_ratio": round(similarity / 100.0, 4),
                    "reason": reason,
                    "match_kind": match_kind,
                }
            )

        candidates.sort(key=lambda c: (c["similarity"], c["upload_date"]), reverse=True)
        return candidates[:_MAX_CANDIDATES]
    except Exception:
        logger.warning("duplicate check failed for %r", course_code, exc_info=True)
        return []


def _score(
    material: StudyMaterial,
    *,
    title: str,
    course_norm: str,
    edition_norm: str,
    file_hash: str | None,
) -> tuple[float, str, str] | None:
    """``(similarity 0-100, reason, match_kind)``, or ``None`` for a non-match."""
    if file_hash and material.file_hash == file_hash:
        return 100.0, "identical file", "exact_file"

    # Everything below this line is a same-course judgement; a hash miss on
    # another course is not a candidate at all.
    if material.course_code_norm != course_norm:
        return None

    score = float(fuzz.token_set_ratio(title, material.title or ""))
    if score < settings.duplicate_title_threshold:
        return None

    # Two listings that both state no edition count as the same edition --
    # for lecture notes, which is most of the catalogue, that is the norm.
    if normalize_edition(material.edition) == edition_norm:
        return (
            score,
            "same course, edition and a near-identical title",
            "same_course_edition",
        )
    return score, "same course and a near-identical title", "similar_title"


# ---------------------------------------------------------------------------
# FR 3.1 -- price suggestion
# ---------------------------------------------------------------------------


async def suggest_price(
    session: AsyncSession,
    *,
    course_code: str,
    edition: str | None = None,
    listing_type: str = ListingType.SELL,
    campus_id: str | None = None,
) -> dict[str, Any]:
    """Median plus the interquartile range over comparable listings.

    Walks the ladder -- course+edition, course,
    department+semester, campus-wide -- and stops at the first rung holding
    ``settings.price_min_sample`` listings. ``basis`` names the rung that
    answered and ``sample_size`` is always the real count, so the UI can say
    "based on 7 similar listings" instead of implying certainty it does not
    have.

    ``listing_type`` is never relaxed: a rental price and a sale price for the
    same book are different numbers, and averaging them helps nobody.
    """
    if listing_type not in PRICED_LISTING_TYPES:
        # EXCHANGE and FREE carry no price by construction (DB CHECK), so
        # there is nothing to suggest and nothing to apologise for.
        return _suggestion([], BASIS_NONE)

    course_norm = normalize_course_code(course_code)
    edition_norm = normalize_edition(edition)
    cutoff = utcnow() - timedelta(days=settings.price_lookback_days)

    base: list[ColumnElement[bool]] = [
        col(StudyMaterial.status).in_(_PRICE_STATUSES),
        col(StudyMaterial.upload_date) >= cutoff,
        col(StudyMaterial.listing_type) == listing_type,
        col(StudyMaterial.price).is_not(None),
    ]
    if campus_id:
        base.append(col(StudyMaterial.campus_id) == campus_id)

    # Rungs 1 and 2 are the same rows, split in Python: edition is stored raw,
    # so "same edition" is a normalize_edition() comparison, not a SQL one.
    course_rows = (
        await session.execute(
            select(StudyMaterial.price, StudyMaterial.edition).where(
                *base, col(StudyMaterial.course_code_norm) == course_norm
            )
        )
    ).all()
    course_prices = [float(price) for price, _ in course_rows if price is not None]
    edition_prices = [
        float(price)
        for price, raw_edition in course_rows
        if price is not None and normalize_edition(raw_edition) == edition_norm
    ]

    widest = course_prices
    if len(edition_prices) >= settings.price_min_sample:
        return _suggestion(edition_prices, BASIS_COURSE_EDITION)
    if len(course_prices) >= settings.price_min_sample:
        return _suggestion(course_prices, BASIS_COURSE)

    # Rung 3 widens to the course's neighbours. The caller only gives us a
    # course code, so the department/semester pair is read off the course's
    # own listings -- a CSE470 book is priced like other final-year CSE books.
    placement = await _course_placement(session, course_norm=course_norm)
    if placement is not None:
        department, semester = placement
        department_prices = await _prices(
            session,
            *base,
            col(StudyMaterial.department) == department,
            col(StudyMaterial.semester) == semester,
        )
        if len(department_prices) >= settings.price_min_sample:
            return _suggestion(department_prices, BASIS_DEPARTMENT)
        widest = max(widest, department_prices, key=len)

    campus_prices = await _prices(session, *base)
    if len(campus_prices) >= settings.price_min_sample:
        return _suggestion(campus_prices, BASIS_CAMPUS)
    widest = max(widest, campus_prices, key=len)

    # Nothing to stand on. Report the widest sample we actually saw so the UI
    # can say *why* there is no number rather than just showing a blank.
    return _suggestion(widest, BASIS_NONE)


async def _prices(
    session: AsyncSession, *conditions: ColumnElement[bool]
) -> list[float]:
    result = await session.execute(select(StudyMaterial.price).where(*conditions))
    return [float(price) for price in result.scalars().all() if price is not None]


async def _course_placement(
    session: AsyncSession, *, course_norm: str
) -> tuple[str, str] | None:
    """The department and semester a course is taught in, per its newest listing."""
    row = (
        await session.execute(
            select(StudyMaterial.department, StudyMaterial.semester)
            .where(col(StudyMaterial.course_code_norm) == course_norm)
            .order_by(col(StudyMaterial.upload_date).desc())
            .limit(1)
        )
    ).first()
    if row is None:
        return None
    department, semester = row
    return (department, semester)


def _suggestion(prices: Sequence[float], basis: str) -> dict[str, Any]:
    """Build the payload. ``sample_size`` is reported even when there is no number."""
    if basis == BASIS_NONE or len(prices) < settings.price_min_sample:
        return {
            "suggested": None,
            "suggested_price": None,
            "low": None,
            "high": None,
            "sample_size": len(prices),
            "basis": BASIS_NONE,
            "currency": CURRENCY,
        }

    ordered = sorted(prices)
    median = round(_percentile(ordered, 0.5), 2)
    return {
        "suggested": median,
        "suggested_price": median,
        "low": round(_percentile(ordered, 0.25), 2),
        "high": round(_percentile(ordered, 0.75), 2),
        "sample_size": len(ordered),
        "basis": basis,
        "currency": CURRENCY,
    }


def _percentile(ordered: Sequence[float], fraction: float) -> float:
    """Linear-interpolated percentile over an already-sorted sample.

    Same method as ``numpy.percentile``'s default, in eight lines and without
    the dependency -- these samples are tens of rows, not millions.
    """
    if not ordered:
        raise ValueError("cannot take a percentile of an empty sample")
    position = (len(ordered) - 1) * fraction
    lower, upper = floor(position), ceil(position)
    if lower == upper:
        return float(ordered[lower])
    return float(
        ordered[lower] * (upper - position) + ordered[upper] * (position - lower)
    )


__all__ = [
    "BASIS_CAMPUS",
    "BASIS_COURSE",
    "BASIS_COURSE_EDITION",
    "BASIS_DEPARTMENT",
    "BASIS_NONE",
    "CURRENCY",
    "check_duplicate",
    "suggest_price",
]
