"""Listing shapes, plus the two recommendation-engine payloads (FR 1.5, 3.1).

Three deliberate omissions from ``MaterialRead``: ``file_path``, ``preview_path``
and ``file_hash``. Storage paths are never exposed -- files stream through an
authorizing endpoint, and a leaked path plus a static
mount is exactly how "preview before you buy" turns into a free download.
``has_preview`` carries the only bit of ``preview_path`` the client needs.

``MaterialCreate`` is not bound directly by the upload route -- multipart means
the router reads ``Form`` fields -- but it is constructed from them so the
price/listing-type rule is validated in one place instead of inline in the
handler. It mirrors the DB CHECK: EXCHANGE and FREE carry no price, SELL and
RENT must.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.util import normalize_course_code
from app.models import PRICED_LISTING_TYPES, ListingStatus, ListingType
from app.schemas.common import ORMModel, Page


def _require_text(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValueError("This field cannot be blank.")
    return stripped


class MaterialCreate(BaseModel):
    """Validation helper for the multipart upload handler."""

    title: str = Field(min_length=3, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    course_code: str = Field(min_length=2, max_length=32)
    department: str = Field(min_length=1, max_length=100)
    semester: str = Field(min_length=1, max_length=40)
    edition: str | None = Field(default=None, max_length=40)
    listing_type: ListingType
    price: float | None = Field(default=None, ge=0)

    @field_validator("title", "course_code", "department", "semester")
    @classmethod
    def _strip_required(cls, value: str) -> str:
        return _require_text(value)

    @field_validator("description", "edition")
    @classmethod
    def _strip_optional(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @model_validator(mode="after")
    def _price_matches_type(self) -> MaterialCreate:
        """Mirror of the ``study_materials`` CHECK constraint.

        Caught here it is a 422 naming the field; caught by SQLite it is a
        500 with an opaque IntegrityError.
        """
        priced = self.listing_type in PRICED_LISTING_TYPES
        if priced and self.price is None:
            raise ValueError(f"A {self.listing_type.value} listing needs a price.")
        if not priced and self.price is not None:
            raise ValueError(
                f"A {self.listing_type.value} listing cannot have a price. Leave it empty."
            )
        return self

    @property
    def course_code_norm(self) -> str:
        return normalize_course_code(self.course_code)


class MaterialUpdate(BaseModel):
    """PATCH body. Editing metadata sends the listing back to PENDING (FR 1.1).

    The price/type rule is re-checked in the router against the *merged* row,
    since a PATCH may change only one half of the pair.
    """

    title: str | None = Field(default=None, min_length=3, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    course_code: str | None = Field(default=None, min_length=2, max_length=32)
    department: str | None = Field(default=None, min_length=1, max_length=100)
    semester: str | None = Field(default=None, min_length=1, max_length=40)
    edition: str | None = Field(default=None, max_length=40)
    listing_type: ListingType | None = None
    price: float | None = Field(default=None, ge=0)

    @field_validator("title", "course_code", "department", "semester")
    @classmethod
    def _strip_required(cls, value: str | None) -> str | None:
        return None if value is None else _require_text(value)


class MaterialRead(ORMModel):
    listing_id: str
    uploader_id: str
    uploader_name: str | None = None
    uploader_rating: float = 0.0
    campus_id: str
    campus_name: str | None = None

    title: str
    description: str | None = None
    course_code: str
    course_code_norm: str
    department: str
    semester: str
    edition: str | None = None

    listing_type: ListingType
    price: float | None = None
    status: ListingStatus

    #: Derived from ``preview_path``; the path itself never leaves the server.
    has_preview: bool = False
    page_count: int | None = None
    upload_date: datetime

    #: Moderation feedback is shown to the uploader so a REJECTED listing
    #: explains itself instead of just disappearing.
    moderated_at: datetime | None = None
    moderation_note: str | None = None

    #: Per-caller, so this is False for anonymous and unauthenticated reads.
    is_bookmarked: bool = False
    review_count: int = 0
    avg_rating: float | None = None


#: The search/browse response (FR 1.2).
MaterialSearchResult = Page[MaterialRead]


# ---------------------------------------------------------------------------
# duplicate detection (FR 1.5)
# ---------------------------------------------------------------------------


class DuplicateCheckRequest(BaseModel):
    """Posted as the upload form fills, and again on submit.

    ``file_hash`` is optional because the client can ask before it has read
    the file; without it the check falls back to course code + title
    similarity.
    """

    title: str = Field(min_length=1, max_length=200)
    course_code: str = Field(min_length=1, max_length=32)
    edition: str | None = Field(default=None, max_length=40)
    file_hash: str | None = Field(default=None, max_length=64)

    @property
    def course_code_norm(self) -> str:
        return normalize_course_code(self.course_code)


class DuplicateCandidate(ORMModel):
    """One suspected duplicate.

    ``reason`` is human-readable on purpose ("identical file", "same course
    and edition, 92% title match") -- the requirement is to *warn*, and a
    warning the uploader cannot evaluate is just a blocked form.
    """

    listing_id: str
    title: str
    course_code: str
    edition: str | None = None
    price: float | None = None
    #: 0.0-1.0. Exact file-hash matches report 1.0.
    similarity: float = Field(ge=0.0, le=1.0)
    reason: str
    uploader_name: str | None = None


class DuplicateCheckResponse(ORMModel):
    candidates: list[DuplicateCandidate] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# price suggestion (FR 3.1)
# ---------------------------------------------------------------------------


class PriceSuggestion(ORMModel):
    """Median + IQR over comparable listings, or an honest nothing.

    All three numbers are ``None`` when ``basis == "insufficient_data"``.
    ``sample_size`` is always returned so the UI can say what the number is
    built on rather than presenting a guess from n=1 as fact.
    """

    suggested: float | None = None
    low: float | None = None
    high: float | None = None
    sample_size: int = Field(default=0, ge=0)
    #: Which rung of the fallback ladder produced this:
    #: "course_edition" | "course" | "department_semester" | "campus" |
    #: "insufficient_data".
    basis: str


__all__ = [
    "DuplicateCandidate",
    "DuplicateCheckRequest",
    "DuplicateCheckResponse",
    "MaterialCreate",
    "MaterialRead",
    "MaterialSearchResult",
    "MaterialUpdate",
    "PriceSuggestion",
]
