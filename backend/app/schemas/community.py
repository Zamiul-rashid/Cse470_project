"""Module 2 shapes: requests, wishlist, chat, reviews, notifications.

Two things worth knowing before reading:

* A ``Request`` is the forward-looking saved search the auto-matcher runs on
  (FR 2.3); a wishlist item is a backward-looking bookmark. Their schemas
  differ accordingly -- ``RequestCreate`` carries *criteria*, while
  ``WishlistItemRead`` just embeds the listing it points at.
* ``ConversationRead`` is written from the caller's point of view. The table
  stores a canonical ``user_a_id < user_b_id`` pair, which is useless to a
  client; the router resolves "the other one" and fills ``other_user_*``.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.core.util import normalize_course_code
from app.models import ListingType, NotificationType, RequestStatus
from app.schemas.common import ORMModel
from app.schemas.material import MaterialRead

# ---------------------------------------------------------------------------
# request board (FR 2.1)
# ---------------------------------------------------------------------------


class RequestCreate(BaseModel):
    """Only ``course_code`` is required -- every other field narrows the match.

    A request with no other criteria still works; it just matches more.
    """

    course_code: str = Field(min_length=2, max_length=32)
    department: str | None = Field(default=None, max_length=100)
    edition: str | None = Field(default=None, max_length=40)
    listing_type: ListingType | None = None
    max_price: float | None = Field(default=None, ge=0)
    #: None means "any campus" -- the matcher skips the campus filter entirely.
    campus_id: str | None = None
    description: str | None = Field(default=None, max_length=2000)

    @field_validator("course_code")
    @classmethod
    def _strip_course_code(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("A course code is required.")
        return stripped

    @property
    def course_code_norm(self) -> str:
        return normalize_course_code(self.course_code)


class RequestUpdate(BaseModel):
    """Edit the criteria, or set ``status`` to CLOSED to take it down."""

    course_code: str | None = Field(default=None, min_length=2, max_length=32)
    department: str | None = Field(default=None, max_length=100)
    edition: str | None = Field(default=None, max_length=40)
    listing_type: ListingType | None = None
    max_price: float | None = Field(default=None, ge=0)
    campus_id: str | None = None
    description: str | None = Field(default=None, max_length=2000)
    status: RequestStatus | None = None


class RequestRead(ORMModel):
    request_id: str
    requester_id: str
    requester_name: str | None = None

    course_code: str
    course_code_norm: str
    department: str | None = None
    edition: str | None = None
    listing_type: ListingType | None = None
    max_price: float | None = None
    campus_id: str | None = None
    campus_name: str | None = None

    description: str | None = None
    status: RequestStatus
    created_at: datetime


# ---------------------------------------------------------------------------
# wishlist (FR 2.4)
# ---------------------------------------------------------------------------


class WishlistItemRead(ORMModel):
    """Embeds the whole listing: the wishlist page renders cards, not ids."""

    listing_id: str
    added_at: datetime
    material: MaterialRead


# ---------------------------------------------------------------------------
# messaging (FR 2.2)
# ---------------------------------------------------------------------------


class ConversationCreate(BaseModel):
    """Open or reuse a thread.

    ``listing_id`` scopes the thread to what is being negotiated, so two
    people can have separate conversations about two different books.
    """

    other_user_id: str
    listing_id: str | None = None


class ConversationRead(ORMModel):
    conversation_id: str
    other_user_id: str
    other_user_name: str | None = None
    listing_id: str | None = None
    listing_title: str | None = None
    last_message_at: datetime | None = None
    #: Truncated server-side; the inbox never needs the full body.
    last_message_preview: str | None = None
    unread_count: int = 0


class MessageCreate(BaseModel):
    """Shared by the REST send path and the ``/ws/chat`` frame decoder."""

    content: str = Field(min_length=1, max_length=4000)

    @field_validator("content")
    @classmethod
    def _strip_content(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("A message cannot be empty.")
        return stripped


class MessageRead(ORMModel):
    message_id: str
    conversation_id: str
    sender_id: str
    sender_name: str | None = None
    content: str
    timestamp: datetime
    read_at: datetime | None = None


# ---------------------------------------------------------------------------
# reviews (FR 2.5)
# ---------------------------------------------------------------------------


class ReviewCreate(BaseModel):
    """The transaction id is the authorisation.

    Reviewer, reviewee and listing are all derived from it server-side -- if
    the client supplied them, anyone could review a stranger.
    """

    transaction_id: str
    rating: int = Field(ge=1, le=5)
    comment: str | None = Field(default=None, max_length=2000)


class ReviewRead(ORMModel):
    review_id: str
    rating: int
    comment: str | None = None
    created_at: datetime
    reviewer_id: str
    reviewer_name: str | None = None
    reviewee_id: str
    listing_id: str
    listing_title: str | None = None


# ---------------------------------------------------------------------------
# notifications (FR 2.3, 3.2)
# ---------------------------------------------------------------------------


class NotificationRead(ORMModel):
    """The durable row behind the socket push, not a transient toast.

    ``ref_type``/``ref_id`` are what the bell menu navigates on.
    """

    notification_id: str
    type: NotificationType
    message: str
    #: 'listing' | 'transaction' | 'conversation' | 'request'
    ref_type: str | None = None
    ref_id: str | None = None
    is_read: bool = False
    created_at: datetime


__all__ = [
    "ConversationCreate",
    "ConversationRead",
    "MessageCreate",
    "MessageRead",
    "NotificationRead",
    "RequestCreate",
    "RequestRead",
    "RequestUpdate",
    "ReviewCreate",
    "ReviewRead",
    "WishlistItemRead",
]
