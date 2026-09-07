/**
 * TypeScript mirrors of `backend/app/schemas/*` (plus the four response models
 * that live inside `backend/app/api/v1/*` -- they are marked below).
 *
 * THE BACKEND IS AUTHORITATIVE. It produces the JSON; this file follows it.
 * If a field here disagrees with a Pydantic model, this file is wrong. Never
 * "fix" a panel by adding a field the server does not send.
 *
 * Regenerate the contract dump before editing (from `backend/`, venv active):
 *   python -c "import json; from app.main import app; print(json.dumps(app.openapi()['components']['schemas'], indent=2))" > API_CONTRACT.json
 *
 * The string unions are the exact vocabularies of `backend/app/models/enums.py`.
 * They are unions rather than TS enums so a value off the wire is assignable
 * without a cast, and so `switch` exhaustiveness checks work.
 *
 * Optionality rule used throughout: a response field is optional (`?`) only
 * when the Pydantic default is `None`; a field whose default is a real value
 * (`0`, `False`, `""`) is always serialised and is therefore required here.
 * Request fields are optional whenever the model gives them any default.
 *
 * Timestamps are ISO-8601 strings as serialised by Pydantic. Use the helpers in
 * ./format.ts to render them -- the backend emits naive UTC, which is why they
 * must not be handed straight to `new Date()`.
 */

// ---------------------------------------------------------------------------
// enumerated vocabularies -- backend/app/models/enums.py
// ---------------------------------------------------------------------------

export type UserRole = 'STUDENT' | 'ADMIN'

export type ListingType = 'SELL' | 'RENT' | 'EXCHANGE' | 'FREE'

export type ListingStatus =
  | 'PENDING'
  | 'APPROVED'
  | 'REJECTED'
  | 'RESERVED'
  | 'COMPLETED'
  | 'REMOVED'

export type TransactionStatus =
  | 'REQUESTED'
  | 'ACCEPTED'
  | 'AWAITING_HANDOFF'
  | 'COMPLETED'
  | 'CANCELLED'

export type RequestStatus = 'OPEN' | 'MATCHED' | 'CLOSED'

export type ReportReason = 'INAPPROPRIATE' | 'FAKE' | 'COPYRIGHT' | 'SPAM' | 'OTHER'

export type ReportStatus = 'OPEN' | 'RESOLVED' | 'DISMISSED'

export type NotificationType =
  | 'WISHLIST_MATCH'
  | 'REQUEST_MATCH'
  | 'RENTAL_DUE'
  | 'RENTAL_OVERDUE'
  | 'NEW_MESSAGE'
  | 'MODERATION_RESULT'
  | 'NEW_REVIEW'
  | 'TRANSACTION_UPDATE'

export type DemandEventType = 'SEARCH' | 'VIEW' | 'REQUEST' | 'WISHLIST_ADD'

/**
 * Rental reminder stages. Server-side only: the scheduler records these on
 * `rentals`, but no response model exposes the column -- `RentalRead` reports
 * `days_remaining` and `is_overdue` instead. Kept because format.ts labels the
 * vocabulary and the enum is part of the domain.
 */
export type ReminderStage = 'NONE' | 'T_MINUS_3' | 'T_MINUS_1' | 'OVERDUE'

/**
 * Frontend-only. `NotificationRead.ref_type` is a plain `str` on the backend;
 * these are the values the services actually write, and they drive
 * click-through navigation. Compare against `Notification.ref_type` -- do not
 * type the field itself with this union.
 */
export type NotificationRefType = 'listing' | 'transaction' | 'conversation'

/** Iteration order for filter dropdowns -- keep the UI order stable. */
export const LISTING_TYPES: readonly ListingType[] = ['SELL', 'RENT', 'EXCHANGE', 'FREE']

export const LISTING_STATUSES: readonly ListingStatus[] = [
  'PENDING',
  'APPROVED',
  'REJECTED',
  'RESERVED',
  'COMPLETED',
  'REMOVED',
]

export const REPORT_REASONS: readonly ReportReason[] = [
  'INAPPROPRIATE',
  'FAKE',
  'COPYRIGHT',
  'SPAM',
  'OTHER',
]

/** Listing types that must carry a price; the other two must not (DB CHECK). */
export const PRICED_LISTING_TYPES: readonly ListingType[] = ['SELL', 'RENT']

export function isPricedListingType(t: ListingType): boolean {
  return PRICED_LISTING_TYPES.includes(t)
}

// ---------------------------------------------------------------------------
// envelopes -- schemas/common.py
// ---------------------------------------------------------------------------

/** `Page[T]`. Every paged endpoint returns exactly this shape. */
export interface Page<T> {
  items: T[]
  total: number
  page: number
  page_size: number
  pages: number
}

/** `MessageResponse` -- the generic acknowledgement body. */
export interface MessageResponse {
  detail: string
}

/** FastAPI's error body. `detail` is a string for our own HTTPExceptions. */
export interface ApiErrorBody {
  detail?: string | { msg?: string; loc?: (string | number)[] }[]
}

// ---------------------------------------------------------------------------
// identity & place -- schemas/campus.py, schemas/user.py
// ---------------------------------------------------------------------------

/** `CampusRead`. */
export interface Campus {
  campus_id: string
  name: string
  location: string
}

/** `UserPublic` -- `GET /users/{id}`. No email, no join date. */
export interface PublicProfile {
  user_id: string
  name: string
  campus_id: string
  campus_name?: string | null
  rating_avg: number
  rating_count: number
  upload_count: number
  role: UserRole
}

/** `UserMe` -- `GET|PATCH /users/me`. `UserPublic` plus the private fields. */
export interface User extends PublicProfile {
  email: string
  created_at: string
}

/**
 * `UserUpdate` -- `PATCH /users/me`. Name and campus only; the API has no
 * password-change field, so no form may send one.
 */
export interface UserUpdate {
  name?: string | null
  campus_id?: string | null
}

// ---------------------------------------------------------------------------
// auth -- schemas/auth.py
// ---------------------------------------------------------------------------

export interface LoginRequest {
  email: string
  password: string
}

export interface RegisterRequest {
  name: string
  email: string
  password: string
  campus_id: string
}

export interface TokenPair {
  access_token: string
  refresh_token: string
  token_type: string
  expires_in: number
}

export interface RefreshRequest {
  refresh_token: string
}

// ---------------------------------------------------------------------------
// module 1 -- listings & discovery -- schemas/material.py
// ---------------------------------------------------------------------------

/**
 * `MaterialRead` -- the one and only material response model. Browse rows,
 * listing detail, the wishlist and the moderation queue are all this shape;
 * there is no summary/detail split on the wire.
 *
 * `file_path`, `preview_path` and `file_hash` are deliberately never exposed.
 */
export interface Material {
  listing_id: string
  uploader_id: string
  uploader_name?: string | null
  uploader_rating: number
  campus_id: string
  campus_name?: string | null
  title: string
  description?: string | null
  course_code: string
  course_code_norm: string
  department: string
  semester: string
  edition?: string | null
  listing_type: ListingType
  price?: number | null
  status: ListingStatus
  has_preview: boolean
  page_count?: number | null
  upload_date: string
  moderated_at?: string | null
  moderation_note?: string | null
  /** Whether the *calling* user has this in their wishlist. */
  is_bookmarked: boolean
  review_count: number
  avg_rating?: number | null
}

/** Metadata half of the multipart `POST /materials` body. */
export interface MaterialCreate {
  title: string
  description?: string | null
  course_code: string
  department: string
  semester: string
  edition?: string | null
  listing_type: ListingType
  price?: number | null
}

/** `PATCH /materials/{id}`. Editing returns the listing to PENDING. */
export interface MaterialUpdate {
  title?: string | null
  description?: string | null
  course_code?: string | null
  department?: string | null
  semester?: string | null
  edition?: string | null
  listing_type?: ListingType | null
  price?: number | null
}

/** FR 1.5 -- `POST /materials/check-duplicate` body. */
export interface DuplicateCheckRequest {
  title: string
  course_code: string
  edition?: string | null
  file_hash?: string | null
}

/** One candidate returned by the duplicate checker. */
export interface DuplicateCandidate {
  listing_id: string
  title: string
  course_code: string
  edition?: string | null
  price?: number | null
  /** 0.0-1.0, NOT a percentage. Multiply by 100 before rendering one. */
  similarity: number
  /** Human-readable: "identical file", "same course and edition, 92% title match". */
  reason: string
  uploader_name?: string | null
}

export interface DuplicateCheckResponse {
  candidates: DuplicateCandidate[]
}

/**
 * FR 3.1 -- `GET /materials/price-suggestion`. All three numbers are null when
 * `basis === 'insufficient_data'`. There is no currency field; the app renders
 * one symbol (see format.ts).
 */
export interface PriceSuggestion {
  suggested?: number | null
  low?: number | null
  high?: number | null
  sample_size: number
  basis: PriceBasis
}

/** The rungs of the fallback ladder in `services/recommendation.py`. */
export type PriceBasis =
  | 'course_edition'
  | 'course'
  | 'department'
  | 'campus'
  | 'insufficient_data'

// ---------------------------------------------------------------------------
// module 2 -- community & communication -- schemas/community.py
// ---------------------------------------------------------------------------

/** `RequestRead` -- the forward-looking saved search (FR 2.1, 2.3). */
export interface RequestRead {
  request_id: string
  requester_id: string
  requester_name?: string | null
  course_code: string
  course_code_norm: string
  department?: string | null
  edition?: string | null
  listing_type?: ListingType | null
  max_price?: number | null
  campus_id?: string | null
  campus_name?: string | null
  description?: string | null
  status: RequestStatus
  created_at: string
}

export interface RequestCreate {
  course_code: string
  department?: string | null
  edition?: string | null
  listing_type?: ListingType | null
  max_price?: number | null
  campus_id?: string | null
  description?: string | null
}

export interface RequestUpdate {
  course_code?: string | null
  department?: string | null
  edition?: string | null
  listing_type?: ListingType | null
  max_price?: number | null
  campus_id?: string | null
  description?: string | null
  status?: RequestStatus | null
}

/** `WishlistItemRead`. Note `material`, not `listing`. */
export interface WishlistItem {
  listing_id: string
  added_at: string
  material: Material
}

/** Body of `POST /wishlist/items` (declared in api/v1/wishlist.py). */
export interface WishlistItemCreate {
  listing_id: string
}

/** `ConversationRead` -- an inbox row (FR 2.2). The counterparty is flattened. */
export interface Conversation {
  conversation_id: string
  other_user_id: string
  other_user_name?: string | null
  listing_id?: string | null
  listing_title?: string | null
  last_message_at?: string | null
  last_message_preview?: string | null
  unread_count: number
}

export interface ConversationCreate {
  /** The counterparty. The server canonicalises the (a, b) ordering. */
  other_user_id: string
  listing_id?: string | null
}

/** `MessageRead`. */
export interface Message {
  message_id: string
  conversation_id: string
  sender_id: string
  sender_name?: string | null
  content: string
  timestamp: string
  read_at?: string | null
}

export interface MessageCreate {
  content: string
}

/** `NotificationRead`. The recipient is the token subject; no `user_id` is sent. */
export interface Notification {
  notification_id: string
  type: NotificationType
  message: string
  /** Free-form on the wire; compare against {@link NotificationRefType}. */
  ref_type?: string | null
  ref_id?: string | null
  is_read: boolean
  created_at: string
}

/** `GET /notifications/unread-count` (declared in api/v1/notifications.py). */
export interface UnreadCountResponse {
  count: number
}

/** `ReviewRead` (FR 2.5). Carries the listing, not the transaction. */
export interface Review {
  review_id: string
  rating: number
  comment?: string | null
  created_at: string
  reviewer_id: string
  reviewer_name?: string | null
  reviewee_id: string
  listing_id: string
  listing_title?: string | null
}

export interface ReviewCreate {
  transaction_id: string
  rating: number
  comment?: string | null
}

// ---------------------------------------------------------------------------
// module 3 -- transactions & trust -- schemas/transaction.py
// ---------------------------------------------------------------------------

/** `TransactionRead`. `role`, `handoff` and `can_review` are computed server-side. */
export interface Transaction {
  transaction_id: string
  listing_id: string
  listing_title?: string | null
  buyer_id: string
  buyer_name?: string | null
  seller_id: string
  seller_name?: string | null
  transaction_type: ListingType
  agreed_price?: number | null
  status: TransactionStatus
  transaction_date: string
  completed_at?: string | null
  /** Which side of the deal the *caller* is on. Never compare ids yourself. */
  role: TransactionRole
  rental?: Rental | null
  /** Present once a handoff exists; drives the QR panel. */
  handoff?: HandoffState | null
  /** The FR 2.5 gate: COMPLETED, caller is a party, caller has not reviewed. */
  can_review: boolean
}

export type TransactionRole = 'buyer' | 'seller'

export interface TransactionCreate {
  listing_id: string
  /** Optional negotiated price; defaults to the listing price. */
  agreed_price?: number | null
  /** RENT only, 1-365. Sets the due date when the handoff completes. */
  rental_days?: number | null
}

/** `PATCH /transactions/{id}/status`. Status only -- there is no note field. */
export interface TransactionStatusUpdate {
  status: TransactionStatus
}

/** `RentalRead` (FR 3.2). `days_remaining` is negative once overdue. */
export interface Rental {
  rental_id: string
  transaction_id: string
  listing_title?: string | null
  start_date: string
  due_date: string
  returned: boolean
  returned_at?: string | null
  days_remaining: number
  /** Decided server-side so no browser timezone can disagree about lateness. */
  is_overdue: boolean
}

/**
 * `HandoffState` -- the flattened, credential-free view of `qr_handoffs` shown
 * to both parties. No token, no hash, no manual code.
 */
export interface HandoffState {
  generated: boolean
  expires_at?: string | null
  verified_by_buyer: boolean
  verified_by_seller: boolean
  verified_at?: string | null
}

/**
 * `QRGenerateResponse` (FR 3.3) -- returned once, to the seller who asked.
 * `qr_png_data_uri` is a `data:` URI; `token` is the signed value the buyer
 * posts back.
 */
export interface QRGenerateResponse {
  qr_png_data_uri: string
  manual_code: string
  expires_at: string
  token: string
}

/** Buyer submits the scanned token, or the manual code. At least one. */
export interface QRVerifyRequest {
  token?: string | null
  manual_code?: string | null
}

/** `ReportRead` (FR 3.5). The resolving admin's id is not exposed. */
export interface Report {
  report_id: string
  listing_id: string
  listing_title?: string | null
  reporter_id: string
  reporter_name?: string | null
  reason: ReportReason
  details?: string | null
  status: ReportStatus
  created_at: string
  resolved_at?: string | null
  resolution_note?: string | null
}

export interface ReportCreate {
  listing_id: string
  reason: ReportReason
  details?: string | null
}

// ---------------------------------------------------------------------------
// module 4 -- admin, analytics & insights -- schemas/admin.py, schemas/analytics.py
// ---------------------------------------------------------------------------

/** `ModerationDecision` -- body of approve/reject. Required on reject. */
export interface ModerationDecision {
  note?: string | null
}

/** `ReportResolution` -- `POST /admin/reports/{id}/resolve`. */
export interface ReportResolution {
  action: 'RESOLVE' | 'DISMISS'
  remove_listing?: boolean
  note?: string | null
}

/** `ApprovalResult` -- `POST /admin/materials/{id}/approve` (api/v1/admin.py). */
export interface ApprovalResult {
  listing: Material
  /** How many students the auto-match notified. "Approved -- 3 students notified". */
  notified: number
}

/** `AdminStats` -- `GET /admin/stats` (api/v1/admin.py). */
export interface AdminStats {
  pending_listings: number
  open_reports: number
  users: number
  listings: number
  transactions: number
}

/** `LeaderboardEntry` (FR 4.2). Rank is assigned server-side after sorting. */
export interface LeaderboardEntry {
  rank: number
  user_id: string
  name: string
  campus_name?: string | null
  approved_uploads: number
  completed_transactions: number
  rating_avg: number
  score: number
}

/** `TrendingCourse` (FR 4.4) -- weighted demand over the rolling window. */
export interface TrendingCourse {
  rank: number
  course_code: string
  department?: string | null
  demand_score: number
  request_count: number
  wishlist_count: number
  search_count: number
  view_count: number
  /** Listings currently APPROVED for this course -- demand without supply. */
  available_listings: number
}

// ---------------------------------------------------------------------------
// query-parameter shapes -- frontend only, mirroring the route signatures
// ---------------------------------------------------------------------------

/** `GET /materials` (FR 1.2, 4.3). `listing_type` may repeat. */
export interface MaterialQuery {
  q?: string
  course_code?: string
  department?: string
  semester?: string
  listing_type?: ListingType | ListingType[]
  campus_id?: string
  min_price?: number
  max_price?: number
  /** Owner or admin only; a 403 otherwise. */
  status?: ListingStatus
  uploader_id?: string
  bookmarked_only?: boolean
  sort?: MaterialSort
  page?: number
  page_size?: number
}

export type MaterialSort = 'newest' | 'oldest' | 'price_asc' | 'price_desc' | 'rating'

/** `GET /requests`. */
export interface RequestQuery {
  status?: RequestStatus
  course_code?: string
  campus_id?: string
  mine?: boolean
  page?: number
  page_size?: number
}

/** `GET /transactions/me`. `role` accepts 'all', which is the default. */
export interface TransactionQuery {
  role?: TransactionRole | 'all'
  status?: TransactionStatus
  page?: number
  page_size?: number
}

/** `GET /analytics/leaderboard` and `/analytics/trending-courses`. */
export interface AnalyticsQuery {
  campus_id?: string
  limit?: number
}

/** `GET /exports/history?format=...` (FR 4.5). */
export type ExportFormat = 'csv' | 'pdf'

// ---------------------------------------------------------------------------
// websocket envelopes -- backend/app/ws/
// ---------------------------------------------------------------------------

/**
 * Frames pushed on `/ws/chat`. The server sends both envelopes in one object
 * (`kind`/`message` for parity with the notifier, `type`/`data` for this file),
 * so either discriminant is safe to switch on.
 */
export type ChatSocketEvent =
  | { kind: 'message'; type: 'message'; message: Message; data: Message }
  | { kind: 'error'; type: 'error'; detail: string; data: { detail: string } }

/** Frames sent by the client on `/ws/chat`. The server reads only these two keys. */
export interface ChatSocketSend {
  conversation_id: string
  content: string
}

/**
 * Frames pushed on `/ws/notifications` (server -> client only).
 *
 * The notification frame carries ONLY the `kind`/`notification` envelope --
 * `services/notifier._frame` does not add `type`/`data`. The unread-count frame
 * carries both, but `kind` is the discriminant that works for either.
 */
export type NotificationSocketEvent =
  | { kind: 'notification'; notification: Notification }
  | { kind: 'unread_count'; count: number }
