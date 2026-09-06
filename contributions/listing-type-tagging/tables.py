"""SQLModel table definitions -- the runtime mirror of alembic/versions/0001_initial.py.

Two deliberate choices:

* **No ``Relationship()`` anywhere.** Lazy loading across an ``AsyncSession``
  raises ``MissingGreenlet`` at unpredictable moments. Every join in this
  codebase is an explicit ``select()``, which is more typing and far fewer
  3am bugs.
* **Tables are created by Alembic, not ``create_all``.** These classes only
  need column names and types to be right; the CHECK/UNIQUE constraints that
  actually enforce the rules live in the migration.
"""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Field, SQLModel

from app.core.util import new_id, utcnow
from app.models.enums import (
    ListingStatus,
    ListingType,
    ReminderStage,
    ReportReason,
    ReportStatus,
    RequestStatus,
    TransactionStatus,
    UserRole,
)

# ---------------------------------------------------------------------------
# identity & place
# ---------------------------------------------------------------------------


class Campus(SQLModel, table=True):
    __tablename__ = "campuses"

    campus_id: str = Field(default_factory=new_id, primary_key=True)
    name: str = Field(index=True)
    location: str


class User(SQLModel, table=True):
    __tablename__ = "users"

    user_id: str = Field(default_factory=new_id, primary_key=True)
    name: str
    email: str = Field(index=True)
    password_hash: str
    role: str = Field(default=UserRole.STUDENT)
    campus_id: str = Field(foreign_key="campuses.campus_id", index=True)
    rating_avg: float = Field(default=0.0)
    rating_count: int = Field(default=0)
    created_at: datetime = Field(default_factory=utcnow)


# ---------------------------------------------------------------------------
# module 1 -- listings
# ---------------------------------------------------------------------------


class StudyMaterial(SQLModel, table=True):
    __tablename__ = "study_materials"

    listing_id: str = Field(default_factory=new_id, primary_key=True)
    #: The association the reference class diagram omitted. Without it the
    #: leaderboard, reviews, transaction history and export all break.
    uploader_id: str = Field(foreign_key="users.user_id", index=True)
    campus_id: str = Field(foreign_key="campuses.campus_id", index=True)

    title: str
    description: str | None = None
    course_code: str
    #: Uppercased, punctuation stripped. See core.util.normalize_course_code.
    course_code_norm: str = Field(index=True)
    department: str
    semester: str
    edition: str | None = None

    listing_type: str = Field(default=ListingType.SELL)
    price: float | None = None
    status: str = Field(default=ListingStatus.PENDING, index=True)

    file_path: str
    preview_path: str | None = None
    page_count: int | None = None
    #: SHA-256 of the upload; an exact match is a near-certain duplicate.
    file_hash: str = Field(index=True)

    upload_date: datetime = Field(default_factory=utcnow)
    moderated_by: str | None = Field(default=None, foreign_key="users.user_id")
    moderated_at: datetime | None = None
    moderation_note: str | None = None


# ---------------------------------------------------------------------------
# module 2 -- community
# ---------------------------------------------------------------------------


class Request(SQLModel, table=True):
    """A wanted-ad *and* a forward-looking saved search.

    The criteria columns are what the auto-match service (FR 2.3) matches on.
    A Wishlist cannot serve that role -- it bookmarks listings that already
    exist and can never match one created tomorrow.
    """

    __tablename__ = "requests"

    request_id: str = Field(default_factory=new_id, primary_key=True)
    requester_id: str = Field(foreign_key="users.user_id", index=True)

    course_code: str
    course_code_norm: str = Field(index=True)
    department: str | None = None
    edition: str | None = None
    listing_type: str | None = None
    max_price: float | None = None
    #: NULL means "any campus".
    campus_id: str | None = Field(default=None, foreign_key="campuses.campus_id")

    description: str | None = None
    status: str = Field(default=RequestStatus.OPEN, index=True)
    created_at: datetime = Field(default_factory=utcnow)


class Wishlist(SQLModel, table=True):
    """Exactly one per user; created during registration."""

    __tablename__ = "wishlists"

    wishlist_id: str = Field(default_factory=new_id, primary_key=True)
    user_id: str = Field(foreign_key="users.user_id", index=True)


class WishlistItem(SQLModel, table=True):
    __tablename__ = "wishlist_items"

    wishlist_id: str = Field(foreign_key="wishlists.wishlist_id", primary_key=True)
    listing_id: str = Field(foreign_key="study_materials.listing_id", primary_key=True)
    added_at: datetime = Field(default_factory=utcnow)


class Conversation(SQLModel, table=True):
    """Thread between two users, optionally about one listing.

    ``user_a_id < user_b_id`` is enforced by a CHECK constraint so the pair is
    canonical and the UNIQUE index actually prevents duplicate threads.
    """

    __tablename__ = "conversations"

    conversation_id: str = Field(default_factory=new_id, primary_key=True)
    user_a_id: str = Field(foreign_key="users.user_id", index=True)
    user_b_id: str = Field(foreign_key="users.user_id", index=True)
    listing_id: str | None = Field(default=None, foreign_key="study_materials.listing_id")
    created_at: datetime = Field(default_factory=utcnow)
    last_message_at: datetime | None = None


class Message(SQLModel, table=True):
    __tablename__ = "messages"

    message_id: str = Field(default_factory=new_id, primary_key=True)
    conversation_id: str = Field(foreign_key="conversations.conversation_id", index=True)
    sender_id: str = Field(foreign_key="users.user_id")
    content: str
    timestamp: datetime = Field(default_factory=utcnow)
    read_at: datetime | None = None


# ---------------------------------------------------------------------------
# module 3 -- transactions & trust
# ---------------------------------------------------------------------------


class Transaction(SQLModel, table=True):
    __tablename__ = "transactions"

    transaction_id: str = Field(default_factory=new_id, primary_key=True)
    listing_id: str = Field(foreign_key="study_materials.listing_id", index=True)
    #: Two distinct roles. The reference diagram had one ambiguous
    #: "participates in" edge, which QR handoff cannot work with.
    buyer_id: str = Field(foreign_key="users.user_id", index=True)
    seller_id: str = Field(foreign_key="users.user_id", index=True)

    transaction_type: str
    agreed_price: float | None = None
    status: str = Field(default=TransactionStatus.REQUESTED, index=True)
    transaction_date: datetime = Field(default_factory=utcnow)
    completed_at: datetime | None = None


class Rental(SQLModel, table=True):
    """0..1 per transaction; created when a RENT transaction completes."""

    __tablename__ = "rentals"

    rental_id: str = Field(default_factory=new_id, primary_key=True)
    transaction_id: str = Field(foreign_key="transactions.transaction_id", index=True)
    start_date: datetime = Field(default_factory=utcnow)
    due_date: datetime = Field(index=True)
    returned: bool = Field(default=False)
    returned_at: datetime | None = None
    #: Guards against a process restart re-sending every reminder (FR 3.2).
    last_reminder_stage: str = Field(default=ReminderStage.NONE)


class QRHandoff(SQLModel, table=True):
    """0..1 per transaction. Signed, short-lived, single-use (FR 3.3)."""

    __tablename__ = "qr_handoffs"

    handoff_id: str = Field(default_factory=new_id, primary_key=True)
    transaction_id: str = Field(foreign_key="transactions.transaction_id", index=True)
    #: The raw token is never stored -- only its SHA-256.
    token_hash: str
    nonce: str = Field(index=True)
    #: Typed fallback for when the camera is unavailable: camera access needs
    #: a secure context.
    manual_code: str
    expires_at: datetime
    consumed_at: datetime | None = None
    verified_by_buyer: bool = Field(default=False)
    verified_by_seller: bool = Field(default=False)
    verified_at: datetime | None = None


class Report(SQLModel, table=True):
    __tablename__ = "reports"

    report_id: str = Field(default_factory=new_id, primary_key=True)
    reporter_id: str = Field(foreign_key="users.user_id", index=True)
    listing_id: str = Field(foreign_key="study_materials.listing_id", index=True)
    reason: str = Field(default=ReportReason.OTHER)
    details: str | None = None
    status: str = Field(default=ReportStatus.OPEN, index=True)
    created_at: datetime = Field(default_factory=utcnow)
    resolved_by: str | None = Field(default=None, foreign_key="users.user_id")
    resolved_at: datetime | None = None
    resolution_note: str | None = None


class Review(SQLModel, table=True):
    """Anchored to the transaction that authorises it.

    ``UNIQUE (transaction_id, reviewer_id)`` is what stops a user reviewing a
    stranger, or reviewing the same counterparty repeatedly.
    """

    __tablename__ = "reviews"

    review_id: str = Field(default_factory=new_id, primary_key=True)
    transaction_id: str = Field(foreign_key="transactions.transaction_id", index=True)
    reviewer_id: str = Field(foreign_key="users.user_id", index=True)
    reviewee_id: str = Field(foreign_key="users.user_id", index=True)
    listing_id: str = Field(foreign_key="study_materials.listing_id", index=True)
    rating: int
    comment: str | None = None
    created_at: datetime = Field(default_factory=utcnow)


# ---------------------------------------------------------------------------
# cross-cutting
# ---------------------------------------------------------------------------


class Notification(SQLModel, table=True):
    """Persisted first, pushed over the socket second.

    ``ref_type``/``ref_id`` are what let the client navigate somewhere useful
    when the notification is clicked.
    """

    __tablename__ = "notifications"

    notification_id: str = Field(default_factory=new_id, primary_key=True)
    user_id: str = Field(foreign_key="users.user_id", index=True)
    type: str
    message: str
    #: 'listing' | 'transaction' | 'conversation' | 'request'
    ref_type: str | None = None
    ref_id: str | None = None
    is_read: bool = Field(default=False, index=True)
    created_at: datetime = Field(default_factory=utcnow)


class CourseDemandEvent(SQLModel, table=True):
    """Append-only demand signal behind the trending dashboard (FR 4.4)."""

    __tablename__ = "course_demand_events"

    event_id: int | None = Field(default=None, primary_key=True)
    course_code_norm: str = Field(index=True)
    campus_id: str | None = Field(default=None, foreign_key="campuses.campus_id")
    event_type: str
    created_at: datetime = Field(default_factory=utcnow)


class RefreshToken(SQLModel, table=True):
    """Server-side handle so ``POST /auth/logout`` can actually revoke.

    A stateless JWT cannot be revoked, and "logout" that leaves a valid token
    in the wild is not logout.
    """

    __tablename__ = "refresh_tokens"

    token_id: str = Field(default_factory=new_id, primary_key=True)
    user_id: str = Field(foreign_key="users.user_id", index=True)
    token_hash: str = Field(index=True)
    expires_at: datetime
    revoked_at: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow)


__all__ = [
    "Campus",
    "Conversation",
    "CourseDemandEvent",
    "Message",
    "Notification",
    "QRHandoff",
    "RefreshToken",
    "Rental",
    "Report",
    "Request",
    "Review",
    "StudyMaterial",
    "Transaction",
    "User",
    "Wishlist",
    "WishlistItem",
]
