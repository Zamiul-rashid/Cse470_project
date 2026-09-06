/**
 * Display helpers. Two rules hold this file together:
 *
 * 1. No component builds a colour class by string concatenation. The maps here
 *    contain every token class *as a literal*, which is what lets Tailwind's
 *    JIT scanner see them.
 * 2. The backend serialises naive UTC datetimes (`2026-08-12T09:30:00`), which
 *    `new Date()` reads as *local* time. Everything goes through
 *    {@link parseDate}, which repairs that.
 */

import type {
  ListingStatus,
  ListingType,
  ReminderStage,
  ReportStatus,
  RequestStatus,
  TransactionStatus,
} from './types'

// ---------------------------------------------------------------------------
// class names
// ---------------------------------------------------------------------------

/** Conditional className joiner. Falsy entries are dropped. */
// Accepts the full set of falsy values a `cond && 'class'` expression can
// produce -- including 0 and '' -- so callers never have to coerce first.
export function cx(...parts: (string | number | false | null | undefined)[]): string {
  return parts.filter((part): part is string => Boolean(part)).join(' ')
}

// ---------------------------------------------------------------------------
// dates
// ---------------------------------------------------------------------------

/** ISO string -> Date, treating a timezone-less timestamp as UTC. */
export function parseDate(value: string | Date | null | undefined): Date | null {
  if (!value) return null
  if (value instanceof Date) return Number.isNaN(value.getTime()) ? null : value

  const hasZone = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(value)
  const iso = hasZone ? value : `${value.replace(' ', 'T')}Z`
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? null : date
}

const dateFmt = new Intl.DateTimeFormat('en-GB', {
  day: 'numeric',
  month: 'short',
  year: 'numeric',
})

const dateTimeFmt = new Intl.DateTimeFormat('en-GB', {
  day: 'numeric',
  month: 'short',
  year: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
})

const timeFmt = new Intl.DateTimeFormat('en-GB', { hour: '2-digit', minute: '2-digit' })

/** "12 Aug 2026". Returns an em dash for null so tables stay aligned. */
export function formatDate(value: string | Date | null | undefined): string {
  const date = parseDate(value)
  return date ? dateFmt.format(date) : '—'
}

/** "12 Aug 2026, 14:05". */
export function formatDateTime(value: string | Date | null | undefined): string {
  const date = parseDate(value)
  return date ? dateTimeFmt.format(date) : '—'
}

/** "14:05". */
export function formatTime(value: string | Date | null | undefined): string {
  const date = parseDate(value)
  return date ? timeFmt.format(date) : '—'
}

const relativeFmt = new Intl.RelativeTimeFormat('en', { numeric: 'auto' })

const RELATIVE_STEPS: [limit: number, seconds: number, unit: Intl.RelativeTimeFormatUnit][] = [
  [60, 1, 'second'],
  [3600, 60, 'minute'],
  [86400, 3600, 'hour'],
  [604800, 86400, 'day'],
  [2629800, 604800, 'week'],
  [31557600, 2629800, 'month'],
  [Infinity, 31557600, 'year'],
]

/** "3 minutes ago", "in 2 days". */
export function relativeTime(value: string | Date | null | undefined): string {
  const date = parseDate(value)
  if (!date) return '—'

  const deltaSeconds = (date.getTime() - Date.now()) / 1000
  const magnitude = Math.abs(deltaSeconds)
  if (magnitude < 45) return 'just now'

  for (const [limit, divisor, unit] of RELATIVE_STEPS) {
    if (magnitude < limit) return relativeFmt.format(Math.round(deltaSeconds / divisor), unit)
  }
  return dateFmt.format(date)
}

/** Whole days from now until `value`. Negative once the date has passed. */
export function daysUntil(value: string | Date | null | undefined): number | null {
  const date = parseDate(value)
  if (!date) return null
  const startOfDay = (d: Date): number =>
    Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate())
  return Math.round((startOfDay(date) - startOfDay(new Date())) / 86400000)
}

/** Rental countdown copy: "Due in 3 days", "Overdue by 2 days" (FR 3.2). */
export function dueLabel(dueDate: string | Date | null | undefined): string {
  const days = daysUntil(dueDate)
  if (days === null) return '—'
  if (days === 0) return 'Due today'
  if (days === 1) return 'Due tomorrow'
  if (days > 1) return `Due in ${days} days`
  if (days === -1) return 'Overdue by 1 day'
  return `Overdue by ${Math.abs(days)} days`
}

// ---------------------------------------------------------------------------
// money & numbers
// ---------------------------------------------------------------------------

const CURRENCY_SYMBOL = '৳'

/** "৳450", "৳1,250.50". Whole amounts drop the decimals. */
export function currency(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  const fractionDigits = Number.isInteger(value) ? 0 : 2
  return `${CURRENCY_SYMBOL}${value.toLocaleString('en-US', {
    minimumFractionDigits: fractionDigits,
    maximumFractionDigits: 2,
  })}`
}

/** Price as it should read on a card: EXCHANGE and FREE carry no number. */
export function formatPrice(listingType: ListingType, price: number | null | undefined): string {
  if (listingType === 'FREE') return 'Free'
  if (listingType === 'EXCHANGE') return 'Swap'
  const formatted = currency(price)
  return listingType === 'RENT' && formatted !== '—' ? `${formatted} / rental` : formatted
}

/** "4.8" -- one decimal, or "New" when nobody has rated yet. */
export function formatRating(avg: number | null | undefined, count?: number | null): string {
  if (count === 0 || avg === null || avg === undefined) return 'New'
  return avg.toFixed(1)
}

export function pluralize(count: number, singular: string, plural?: string): string {
  return `${count} ${count === 1 ? singular : (plural ?? `${singular}s`)}`
}

/** "AB" -- avatar fallback. */
export function initials(name: string | null | undefined): string {
  if (!name) return '?'
  const words = name.trim().split(/\s+/).slice(0, 2)
  return words.map((w) => w.charAt(0).toUpperCase()).join('') || '?'
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

/** SCREAMING_SNAKE -> "Screaming snake". Fallback for unmapped enum values. */
export function humanizeEnum(value: string): string {
  const lower = value.replace(/_/g, ' ').toLowerCase()
  return lower.charAt(0).toUpperCase() + lower.slice(1)
}

// ---------------------------------------------------------------------------
// listing types (FR 1.3)
// ---------------------------------------------------------------------------

const LISTING_TYPE_LABELS: Record<ListingType, string> = {
  SELL: 'For sale',
  RENT: 'For rent',
  EXCHANGE: 'Exchange',
  FREE: 'Free',
}

/** Soft chip classes -- pair with the `.pill` component class. */
const LISTING_TYPE_CLASSES: Record<ListingType, string> = {
  SELL: 'bg-listing-sell/10 text-listing-sell ring-listing-sell/20',
  RENT: 'bg-listing-rent/10 text-listing-rent ring-listing-rent/20',
  EXCHANGE: 'bg-listing-exchange/10 text-listing-exchange ring-listing-exchange/20',
  FREE: 'bg-listing-free/10 text-listing-free ring-listing-free/20',
}

/** Solid fill -- accent bars, dots, legend swatches. */
const LISTING_TYPE_ACCENTS: Record<ListingType, string> = {
  SELL: 'bg-listing-sell',
  RENT: 'bg-listing-rent',
  EXCHANGE: 'bg-listing-exchange',
  FREE: 'bg-listing-free',
}

export function listingTypeLabel(type: ListingType): string {
  return LISTING_TYPE_LABELS[type] ?? humanizeEnum(type)
}

export function listingTypeColor(type: ListingType): string {
  return LISTING_TYPE_CLASSES[type] ?? 'bg-slate-100 text-slate-700 ring-slate-200'
}

export function listingTypeAccent(type: ListingType): string {
  return LISTING_TYPE_ACCENTS[type] ?? 'bg-slate-400'
}

// ---------------------------------------------------------------------------
// statuses
// ---------------------------------------------------------------------------

export type AnyStatus =
  | ListingStatus
  | TransactionStatus
  | RequestStatus
  | ReportStatus
  | ReminderStage

/** Which vocabulary a status came from. Only OPEN is genuinely ambiguous. */
export type StatusKind = 'listing' | 'transaction' | 'request' | 'report'

type StateToken = 'pending' | 'approved' | 'rejected' | 'reserved' | 'completed' | 'removed'

const STATE_CLASSES: Record<StateToken, string> = {
  pending: 'bg-state-pending/10 text-state-pending ring-state-pending/20',
  approved: 'bg-state-approved/10 text-state-approved ring-state-approved/20',
  rejected: 'bg-state-rejected/10 text-state-rejected ring-state-rejected/20',
  reserved: 'bg-state-reserved/10 text-state-reserved ring-state-reserved/20',
  completed: 'bg-state-completed/10 text-state-completed ring-state-completed/20',
  removed: 'bg-state-removed/10 text-state-removed ring-state-removed/20',
}

const STATE_ACCENTS: Record<StateToken, string> = {
  pending: 'bg-state-pending',
  approved: 'bg-state-approved',
  rejected: 'bg-state-rejected',
  reserved: 'bg-state-reserved',
  completed: 'bg-state-completed',
  removed: 'bg-state-removed',
}

const STATUS_LABELS: Record<string, string> = {
  // listings
  PENDING: 'Pending review',
  APPROVED: 'Approved',
  REJECTED: 'Rejected',
  RESERVED: 'Reserved',
  COMPLETED: 'Completed',
  REMOVED: 'Removed',
  // transactions
  REQUESTED: 'Requested',
  ACCEPTED: 'Accepted',
  AWAITING_HANDOFF: 'Awaiting handoff',
  CANCELLED: 'Cancelled',
  // requests
  OPEN: 'Open',
  MATCHED: 'Matched',
  CLOSED: 'Closed',
  // reports
  RESOLVED: 'Resolved',
  DISMISSED: 'Dismissed',
  // rental reminder stages
  NONE: 'No reminder sent',
  T_MINUS_3: 'Reminded (3 days out)',
  T_MINUS_1: 'Reminded (1 day out)',
  OVERDUE: 'Overdue notice sent',
}

const STATUS_TOKENS: Record<string, StateToken> = {
  PENDING: 'pending',
  APPROVED: 'approved',
  REJECTED: 'rejected',
  RESERVED: 'reserved',
  COMPLETED: 'completed',
  REMOVED: 'removed',

  REQUESTED: 'pending',
  ACCEPTED: 'reserved',
  AWAITING_HANDOFF: 'reserved',
  CANCELLED: 'removed',

  OPEN: 'reserved',
  MATCHED: 'approved',
  CLOSED: 'removed',

  RESOLVED: 'approved',
  DISMISSED: 'removed',

  NONE: 'removed',
  T_MINUS_3: 'pending',
  T_MINUS_1: 'pending',
  OVERDUE: 'rejected',
}

export function statusLabel(status: AnyStatus, kind?: StatusKind): string {
  if (kind === 'report' && status === 'OPEN') return 'Awaiting review'
  if (kind === 'request' && status === 'OPEN') return 'Looking for this'
  return STATUS_LABELS[status] ?? humanizeEnum(status)
}

export function statusColor(status: AnyStatus, kind?: StatusKind): string {
  if (kind === 'report' && status === 'OPEN') return STATE_CLASSES.pending
  const token = STATUS_TOKENS[status]
  return token ? STATE_CLASSES[token] : 'bg-slate-100 text-slate-700 ring-slate-200'
}

export function statusAccent(status: AnyStatus, kind?: StatusKind): string {
  if (kind === 'report' && status === 'OPEN') return STATE_ACCENTS.pending
  const token = STATUS_TOKENS[status]
  return token ? STATE_ACCENTS[token] : 'bg-slate-400'
}

// ---------------------------------------------------------------------------
// misc labels
// ---------------------------------------------------------------------------

const NOTIFICATION_LABELS: Record<string, string> = {
  WISHLIST_MATCH: 'Wishlist match',
  REQUEST_MATCH: 'Request match',
  RENTAL_DUE: 'Rental due',
  RENTAL_OVERDUE: 'Rental overdue',
  NEW_MESSAGE: 'New message',
  MODERATION_RESULT: 'Moderation result',
  NEW_REVIEW: 'New review',
  TRANSACTION_UPDATE: 'Transaction update',
}

export function notificationLabel(type: string): string {
  return NOTIFICATION_LABELS[type] ?? humanizeEnum(type)
}

const REPORT_REASON_LABELS: Record<string, string> = {
  INAPPROPRIATE: 'Inappropriate content',
  FAKE: 'Fake or misleading',
  COPYRIGHT: 'Copyright violation',
  SPAM: 'Spam',
  OTHER: 'Something else',
}

export function reportReasonLabel(reason: string): string {
  return REPORT_REASON_LABELS[reason] ?? humanizeEnum(reason)
}

// Keys are the BASIS_* constants emitted by services/recommendation.py. They are
// the rungs of the price-suggestion fallback ladder, widest match first.
const PRICE_BASIS_LABELS: Record<string, string> = {
  course_edition: 'same course and edition',
  course: 'same course',
  department: 'same department and semester',
  campus: 'campus-wide listings',
  insufficient_data: 'not enough similar listings yet',
}

export function priceBasisLabel(basis: string): string {
  return PRICE_BASIS_LABELS[basis] ?? humanizeEnum(basis)
}
