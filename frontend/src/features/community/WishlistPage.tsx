import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { Button } from '../../components/Button'
import { EmptyState } from '../../components/EmptyState'
import { ErrorBox } from '../../components/ErrorBox'
import { ListingCard } from '../../components/ListingCard'
import { PageSpinner } from '../../components/Spinner'
import { useToast } from '../../components/Toast'
import { api } from '../../lib/api'
import { pluralize } from '../../lib/format'
import type { Material, Page } from '../../lib/types'

/**
 * FR 2.4 -- saved listings.
 *
 * A Wishlist is the *backward-looking* bookmark list: it holds listings that
 * already exist. The forward-looking half -- "tell me when something like this
 * appears" -- is the request board (FR 2.1), which is why the empty state
 * points at both Browse and Requests.
 */

const wishlistKey = ['wishlist', 'items'] as const

/**
 * `GET /wishlist/items` answers with `Page[MaterialRead]` -- full listing cards,
 * not bookmark rows. There is no wrapper object and therefore no `added_at`;
 * the server sorts newest-saved first and sets `is_bookmarked` on every row.
 */
type WishlistResponse = Material[] | Page<Material>

/** The endpoint may page or may return a bare array; accept either. */
function itemsOf(data: WishlistResponse | undefined): Material[] {
  if (!data) return []
  return Array.isArray(data) ? data : data.items
}

export default function WishlistPage() {
  const queryClient = useQueryClient()
  const toast = useToast()
  const [pendingId, setPendingId] = useState<string | null>(null)

  const wishlistQuery = useQuery({
    queryKey: wishlistKey,
    queryFn: () => api.get<WishlistResponse>('/wishlist/items'),
  })

  const removeItem = useMutation({
    mutationFn: (listingId: string) => api.del<void>(`/wishlist/items/${listingId}`),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: wishlistKey })
      // The browse grid draws its bookmark icons from the listing payload.
      void queryClient.invalidateQueries({ queryKey: ['materials'] })
      toast.success('Removed from your wishlist')
    },
    onError: (error: unknown) => toast.fromError(error, 'Could not remove that listing'),
    onSettled: () => setPendingId(null),
  })

  const remove = (listing: Material): void => {
    setPendingId(listing.listing_id)
    removeItem.mutate(listing.listing_id)
  }

  const items = itemsOf(wishlistQuery.data)

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-slate-900">Wishlist</h1>
          <p className="mt-1.5 max-w-2xl text-sm leading-relaxed text-slate-500">
            Listings you have saved for later. They stay here until you remove them or the material
            is taken.
          </p>
        </div>
        {items.length > 0 ? (
          <p className="text-sm text-slate-500">{pluralize(items.length, 'saved listing')}</p>
        ) : null}
      </header>

      {wishlistQuery.isPending ? (
        <PageSpinner label="Loading your wishlist" />
      ) : wishlistQuery.isError ? (
        <ErrorBox error={wishlistQuery.error} onRetry={() => void wishlistQuery.refetch()} />
      ) : items.length === 0 ? (
        <EmptyState
          title="Nothing saved yet"
          description="Bookmark a listing while you browse and it lands here, so you can compare a few before you message anyone."
          action={
            <div className="flex flex-wrap justify-center gap-2">
              <Link to="/" className="btn btn-primary">
                Browse materials
              </Link>
              <Link to="/requests" className="btn btn-ghost">
                Post a request instead
              </Link>
            </div>
          }
        />
      ) : (
        <ul className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {items.map((listing) => (
            <li key={listing.listing_id} className="flex">
              <ListingCard
                className="w-full"
                listing={listing}
                onToggleWishlist={remove}
                wishlistPending={pendingId === listing.listing_id}
                actions={
                  <>
                    <Link
                      to={`/materials/${listing.listing_id}`}
                      className="btn btn-ghost px-2.5 py-1.5 text-xs"
                    >
                      View
                    </Link>
                    <Button
                      variant="subtle"
                      size="sm"
                      loading={pendingId === listing.listing_id}
                      onClick={() => remove(listing)}
                    >
                      Remove
                    </Button>
                  </>
                }
              />
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
