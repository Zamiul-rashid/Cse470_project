"""Enumerated vocabularies.

SQLite has no native enum type, so every one of these is stored as TEXT with a
CHECK constraint in the migration and mapped to a ``str`` Enum here. Keep the
two in sync -- the CHECK constraint is what actually stops bad data.
"""

from __future__ import annotations

from enum import Enum


class UserRole(str, Enum):
    STUDENT = "STUDENT"
    ADMIN = "ADMIN"


class ListingType(str, Enum):
    SELL = "SELL"
    RENT = "RENT"
    EXCHANGE = "EXCHANGE"
    FREE = "FREE"


#: Listing types that must carry a price. The others must not (DB CHECK).
PRICED_LISTING_TYPES: frozenset[str] = frozenset({ListingType.SELL, ListingType.RENT})


class ListingStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    RESERVED = "RESERVED"
    COMPLETED = "COMPLETED"
    REMOVED = "REMOVED"


#: Statuses a non-owner is allowed to see in browse/search results.
PUBLIC_LISTING_STATUSES: tuple[str, ...] = (
    ListingStatus.APPROVED,
    ListingStatus.RESERVED,
)


class TransactionStatus(str, Enum):
    REQUESTED = "REQUESTED"
    ACCEPTED = "ACCEPTED"
    AWAITING_HANDOFF = "AWAITING_HANDOFF"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


#: The only legal moves. Every illegal transition is a 409 from the transaction
#: service, never a 500.
TRANSACTION_TRANSITIONS: dict[str, tuple[str, ...]] = {
    TransactionStatus.REQUESTED: (
        TransactionStatus.ACCEPTED,
        TransactionStatus.CANCELLED,
    ),
    TransactionStatus.ACCEPTED: (
        TransactionStatus.AWAITING_HANDOFF,
        TransactionStatus.CANCELLED,
    ),
    TransactionStatus.AWAITING_HANDOFF: (
        TransactionStatus.COMPLETED,
        TransactionStatus.CANCELLED,
    ),
    TransactionStatus.COMPLETED: (),
    TransactionStatus.CANCELLED: (),
}


class RequestStatus(str, Enum):
    OPEN = "OPEN"
    MATCHED = "MATCHED"
    CLOSED = "CLOSED"


class ReportStatus(str, Enum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"
    DISMISSED = "DISMISSED"


class ReportReason(str, Enum):
    INAPPROPRIATE = "INAPPROPRIATE"
    FAKE = "FAKE"
    COPYRIGHT = "COPYRIGHT"
    SPAM = "SPAM"
    OTHER = "OTHER"


class NotificationType(str, Enum):
    WISHLIST_MATCH = "WISHLIST_MATCH"
    REQUEST_MATCH = "REQUEST_MATCH"
    RENTAL_DUE = "RENTAL_DUE"
    RENTAL_OVERDUE = "RENTAL_OVERDUE"
    NEW_MESSAGE = "NEW_MESSAGE"
    MODERATION_RESULT = "MODERATION_RESULT"
    NEW_REVIEW = "NEW_REVIEW"
    TRANSACTION_UPDATE = "TRANSACTION_UPDATE"


class DemandEventType(str, Enum):
    SEARCH = "SEARCH"
    VIEW = "VIEW"
    REQUEST = "REQUEST"
    WISHLIST_ADD = "WISHLIST_ADD"


#: Weights behind the trending-courses dashboard (FR 4.4). A student who posts
#: a wanted-request is a far stronger demand signal than one who scrolls past.
DEMAND_WEIGHTS: dict[str, float] = {
    DemandEventType.REQUEST: 3.0,
    DemandEventType.WISHLIST_ADD: 2.0,
    DemandEventType.SEARCH: 1.0,
    DemandEventType.VIEW: 0.5,
}


class ReminderStage(str, Enum):
    NONE = "NONE"
    T_MINUS_3 = "T_MINUS_3"
    T_MINUS_1 = "T_MINUS_1"
    OVERDUE = "OVERDUE"


#: Ordering used to guarantee a reminder never fires twice for the same stage
#: after a process restart (FR 3.2).
REMINDER_STAGE_ORDER: dict[str, int] = {
    ReminderStage.NONE: 0,
    ReminderStage.T_MINUS_3: 1,
    ReminderStage.T_MINUS_1: 2,
    ReminderStage.OVERDUE: 3,
}
