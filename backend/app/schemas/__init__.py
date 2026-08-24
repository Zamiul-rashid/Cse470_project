"""API request/response models. Import from here, never from the submodules.

Kept separate from ``app.models`` so the wire format is a deliberate choice
rather than whatever the table happens to hold: ``password_hash``,
``file_path``, ``file_hash``, ``token_hash`` and the QR ``manual_code`` all
exist in the database and none of them appear in a response.
"""

from app.schemas.admin import ModerationDecision, ReportResolution
from app.schemas.analytics import LeaderboardEntry, TrendingCourse
from app.schemas.auth import (
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    RegisterRequest,
    TokenPair,
)
from app.schemas.campus import CampusRead
from app.schemas.common import (
    EMAIL_RE,
    MessageResponse,
    ORMModel,
    Page,
    validate_email,
)
from app.schemas.community import (
    ConversationCreate,
    ConversationRead,
    MessageCreate,
    MessageRead,
    NotificationRead,
    RequestCreate,
    RequestRead,
    RequestUpdate,
    ReviewCreate,
    ReviewRead,
    WishlistItemRead,
)
from app.schemas.material import (
    DuplicateCandidate,
    DuplicateCheckRequest,
    DuplicateCheckResponse,
    MaterialCreate,
    MaterialRead,
    MaterialSearchResult,
    MaterialUpdate,
    PriceSuggestion,
)
from app.schemas.transaction import (
    HandoffState,
    QRGenerateResponse,
    QRVerifyRequest,
    RentalRead,
    ReportCreate,
    ReportRead,
    TransactionCreate,
    TransactionRead,
    TransactionStatusUpdate,
)
from app.schemas.user import UserMe, UserPublic, UserUpdate

__all__ = [
    "EMAIL_RE",
    "CampusRead",
    "ConversationCreate",
    "ConversationRead",
    "DuplicateCandidate",
    "DuplicateCheckRequest",
    "DuplicateCheckResponse",
    "HandoffState",
    "LeaderboardEntry",
    "LoginRequest",
    "LogoutRequest",
    "MaterialCreate",
    "MaterialRead",
    "MaterialSearchResult",
    "MaterialUpdate",
    "MessageCreate",
    "MessageRead",
    "MessageResponse",
    "ModerationDecision",
    "NotificationRead",
    "ORMModel",
    "Page",
    "PriceSuggestion",
    "QRGenerateResponse",
    "QRVerifyRequest",
    "RefreshRequest",
    "RegisterRequest",
    "RentalRead",
    "ReportCreate",
    "ReportRead",
    "ReportResolution",
    "RequestCreate",
    "RequestRead",
    "RequestUpdate",
    "ReviewCreate",
    "ReviewRead",
    "TokenPair",
    "TransactionCreate",
    "TransactionRead",
    "TransactionStatusUpdate",
    "TrendingCourse",
    "UserMe",
    "UserPublic",
    "UserUpdate",
    "WishlistItemRead",
    "validate_email",
]
