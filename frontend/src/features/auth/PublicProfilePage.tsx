import { Link, useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'

import { EmptyState } from '../../components/EmptyState'
import { ErrorBox } from '../../components/ErrorBox'
import { ListingCard } from '../../components/ListingCard'
import { PageSpinner } from '../../components/Spinner'
import { StarRating } from '../../components/StarRating'
import { api } from '../../lib/api'
import { useAuth } from '../../lib/auth'
import { formatRating, initials, pluralize, relativeTime } from '../../lib/format'
import type { Material, Page, PublicProfile, Review } from '../../lib/types'

/** How many of this user's newest live listings to show. */
const LISTING_LIMIT = 6
const REVIEW_LIMIT = 10

function StatTile({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg bg-slate-50 px-4 py-3 text-center ring-1 ring-inset ring-slate-200">
      <p className="text-lg font-semibold tabular-nums text-slate-900">{value}</p>
      <p className="mt-0.5 text-xs font-medium uppercase tracking-wide text-slate-500">{label}</p>
    </div>
  )
}

export default function PublicProfilePage() {
  const { id = '' } = useParams<{ id: string }>()
  const { user } = useAuth()
  const isSelf = Boolean(user && user.user_id === id)

  const profileQuery = useQuery({
    queryKey: ['users', 'profile', id],
    queryFn: () => api.get<PublicProfile>(`/users/${id}`),
    enabled: Boolean(id),
  })

  const reviewsQuery = useQuery({
    queryKey: ['users', 'reviews', id],
    queryFn: () =>
      api.get<Page<Review>>(`/users/${id}/reviews`, { query: { page: 1, page_size: REVIEW_LIMIT } }),
    enabled: Boolean(id),
  })

  // `GET /materials` only ever returns publicly visible listings (APPROVED and
  // RESERVED), so this is exactly "their live listings" without extra filtering.
  const listingsQuery = useQuery({
    queryKey: ['materials', 'by-uploader', id],
    queryFn: () =>
      api.get<Page<Material>>('/materials', {
        query: { uploader_id: id, sort: 'newest', page: 1, page_size: LISTING_LIMIT },
      }),
    enabled: Boolean(id),
  })

  if (profileQuery.isPending) return <PageSpinner label="Loading profile" />

  if (profileQuery.isError) {
    return (
      <ErrorBox
        error={profileQuery.error}
        title="Could not load this profile"
        onRetry={() => void profileQuery.refetch()}
      />
    )
  }

  const profile = profileQuery.data
  if (!profile) return <PageSpinner label="Loading profile" />

  const reviews = reviewsQuery.data?.items ?? []
  const listings = listingsQuery.data?.items ?? []

  return (
    <div className="space-y-6">
      {/* ------------------------------------------------------------ header */}
      <header className="card p-6">
        <div className="flex flex-wrap items-start gap-5">
          <span className="flex h-16 w-16 shrink-0 items-center justify-center rounded-full bg-brand-100 text-xl font-semibold text-brand-800">
            {initials(profile.name)}
          </span>

          <div className="min-w-0 flex-1">
            <h1 className="text-2xl font-semibold tracking-tight text-slate-900">{profile.name}</h1>
            {/* The public projection carries no join date -- only `UserMe` has one. */}
            <p className="mt-1 text-sm text-slate-500">
              {profile.campus_name ?? 'Campus unknown'}
            </p>
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <StarRating value={profile.rating_avg} size="sm" count={null} />
              <span className="text-sm font-medium text-slate-700">
                {formatRating(profile.rating_avg, profile.rating_count)}
              </span>
              <span className="text-sm text-slate-400">
                {profile.rating_count === 0
                  ? 'no reviews yet'
                  : `from ${pluralize(profile.rating_count, 'review')}`}
              </span>
            </div>
          </div>

          {isSelf ? (
            <Link to="/profile" className="btn btn-ghost">
              Edit your profile
            </Link>
          ) : null}
        </div>

        <div className="mt-6 grid grid-cols-2 gap-3 sm:grid-cols-3">
          <StatTile label="Uploads" value={String(profile.upload_count)} />
          <StatTile label="Reviews" value={String(profile.rating_count)} />
          <StatTile label="Rating" value={formatRating(profile.rating_avg, profile.rating_count)} />
        </div>
      </header>

      {/* ---------------------------------------------------- live listings */}
      <section>
        <div className="mb-3 flex items-center justify-between gap-3">
          <h2 className="text-base font-semibold text-slate-900">Live listings</h2>
          {listingsQuery.data && listingsQuery.data.total > LISTING_LIMIT ? (
            <p className="text-xs text-slate-500">
              Showing the newest {LISTING_LIMIT} of {listingsQuery.data.total}
            </p>
          ) : null}
        </div>

        {listingsQuery.isPending ? (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {[0, 1, 2].map((i) => (
              <div key={i} className="card h-48 animate-pulse bg-slate-100" />
            ))}
          </div>
        ) : listingsQuery.isError ? (
          <ErrorBox
            error={listingsQuery.error}
            title="Could not load their listings"
            onRetry={() => void listingsQuery.refetch()}
          />
        ) : listings.length === 0 ? (
          <EmptyState
            title="Nothing listed right now"
            description={
              isSelf
                ? 'Anything you upload appears here once a moderator approves it.'
                : `${profile.name} has no listings available at the moment.`
            }
            action={
              isSelf ? (
                <Link to="/upload" className="btn btn-primary">
                  Upload material
                </Link>
              ) : undefined
            }
          />
        ) : (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {listings.map((listing) => (
              <ListingCard key={listing.listing_id} listing={listing} />
            ))}
          </div>
        )}
      </section>

      {/* ---------------------------------------------------------- reviews */}
      <section>
        <h2 className="mb-3 text-base font-semibold text-slate-900">
          Reviews {reviewsQuery.data ? `(${reviewsQuery.data.total})` : ''}
        </h2>

        {reviewsQuery.isPending ? (
          <div className="card h-32 animate-pulse bg-slate-100" />
        ) : reviewsQuery.isError ? (
          <ErrorBox
            error={reviewsQuery.error}
            title="Could not load reviews"
            onRetry={() => void reviewsQuery.refetch()}
          />
        ) : reviews.length === 0 ? (
          <EmptyState
            title="No reviews yet"
            description="A review can only be left after a transaction between two students completes, so this fills up as they trade."
          />
        ) : (
          <ul className="card divide-y divide-slate-100">
            {reviews.map((review) => (
              <li key={review.review_id} className="p-4 sm:p-5">
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                  <StarRating value={review.rating} size="sm" count={null} />
                  <Link
                    to={`/users/${review.reviewer_id}`}
                    className="text-sm font-medium text-slate-800 hover:text-brand-700"
                  >
                    {review.reviewer_name ?? 'A student'}
                  </Link>
                  <span className="text-xs text-slate-400">{relativeTime(review.created_at)}</span>
                </div>

                {review.comment ? (
                  <p className="mt-2 text-sm leading-relaxed text-slate-600">{review.comment}</p>
                ) : (
                  <p className="mt-2 text-sm italic text-slate-400">No comment left.</p>
                )}

                {review.listing_title ? (
                  <p className="mt-2 text-xs text-slate-500">
                    On{' '}
                    <Link
                      to={`/materials/${review.listing_id}`}
                      className="font-medium text-brand-700 hover:text-brand-800"
                    >
                      {review.listing_title}
                    </Link>
                  </p>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  )
}
