import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'

import {
  cx,
  formatPrice,
  formatRating,
  listingTypeAccent,
  relativeTime,
} from '../lib/format'
import type { Material } from '../lib/types'
import { ListingTypePill } from './Pill'
import { StatusPill } from './StatusPill'

export interface ListingCardProps {
  listing: Material
  /** Show the moderation status -- my-listings and the admin queue want this. */
  showStatus?: boolean
  /** Wire up the bookmark button (FR 2.4). Omit to hide it entirely. */
  onToggleWishlist?: (listing: Material) => void
  wishlistPending?: boolean
  /** Footer actions, e.g. Edit / Delete on the owner's own listings. */
  actions?: ReactNode
  className?: string
}

/** One study material in the browse grid, wishlist, or my-listings page. */
export function ListingCard({
  listing,
  showStatus = false,
  onToggleWishlist,
  wishlistPending = false,
  actions,
  className,
}: ListingCardProps) {
  const bookmarked = Boolean(listing.is_bookmarked)

  return (
    <article className={cx('card group relative flex flex-col overflow-hidden', className)}>
      {/* Type accent -- the fastest way to read a grid of mixed listing types. */}
      <span aria-hidden="true" className={cx('h-1 w-full', listingTypeAccent(listing.listing_type))} />

      <div className="flex flex-1 flex-col p-4">
        <div className="mb-2 flex flex-wrap items-center gap-1.5">
          <ListingTypePill type={listing.listing_type} />
          {showStatus ? <StatusPill status={listing.status} kind="listing" /> : null}
        </div>

        <h3 className="text-sm font-semibold leading-snug text-slate-900">
          <Link
            to={`/materials/${listing.listing_id}`}
            className="transition-colors after:absolute after:inset-0 group-hover:text-brand-700"
          >
            {listing.title}
          </Link>
        </h3>

        <p className="mt-1 text-xs text-slate-500">
          <span className="font-medium text-slate-700">{listing.course_code}</span>
          {' · '}
          {listing.department}
          {' · '}
          {listing.semester}
          {listing.edition ? ` · ${listing.edition}` : ''}
        </p>

        <div className="mt-3 flex items-end justify-between gap-3">
          <p className="text-base font-semibold text-slate-900">
            {formatPrice(listing.listing_type, listing.price)}
          </p>
          {listing.page_count ? (
            <p className="text-xs text-slate-400">{listing.page_count} pages</p>
          ) : null}
        </div>

        <div className="mt-3 flex items-center justify-between gap-2 border-t border-slate-100 pt-3 text-xs text-slate-500">
          <span className="truncate">
            <Link
              to={`/users/${listing.uploader_id}`}
              className="relative z-10 font-medium text-slate-600 hover:text-brand-700"
            >
              {listing.uploader_name ?? 'Unknown'}
            </Link>
            {listing.uploader_rating !== null && listing.uploader_rating !== undefined ? (
              <span className="ml-1.5 text-amber-500">
                ★ {formatRating(listing.uploader_rating)}
              </span>
            ) : null}
          </span>
          <span className="shrink-0">{relativeTime(listing.upload_date)}</span>
        </div>

        {actions ? <div className="relative z-10 mt-3 flex gap-2">{actions}</div> : null}
      </div>

      {onToggleWishlist ? (
        <button
          type="button"
          disabled={wishlistPending}
          aria-pressed={bookmarked}
          aria-label={bookmarked ? 'Remove from wishlist' : 'Save to wishlist'}
          onClick={() => onToggleWishlist(listing)}
          className={cx(
            'absolute right-3 top-3 z-10 rounded-full bg-white/90 p-1.5 shadow-sm ring-1 ring-slate-200 transition-colors disabled:opacity-50',
            bookmarked ? 'text-brand-600' : 'text-slate-400 hover:text-brand-600',
          )}
        >
          <svg
            aria-hidden="true"
            viewBox="0 0 20 20"
            fill={bookmarked ? 'currentColor' : 'none'}
            stroke="currentColor"
            strokeWidth={1.5}
            className="h-4 w-4"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M5.5 3.5h9a1 1 0 0 1 1 1v12l-5.5-3.2L4.5 16.5v-12a1 1 0 0 1 1-1Z"
            />
          </svg>
        </button>
      ) : null}
    </article>
  )
}

export default ListingCard
