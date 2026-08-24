import { useEffect, useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { Button } from '../../components/Button'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import { EmptyState } from '../../components/EmptyState'
import { ErrorBox } from '../../components/ErrorBox'
import { Input } from '../../components/Input'
import { ListingCard } from '../../components/ListingCard'
import { Modal } from '../../components/Modal'
import { Pagination } from '../../components/Pagination'
import { StatusPill } from '../../components/StatusPill'
import { TextArea } from '../../components/TextArea'
import { useToast } from '../../components/Toast'
import { ApiError, api } from '../../lib/api'
import { useAuth } from '../../lib/auth'
import { cx, listingTypeColor, listingTypeLabel, pluralize } from '../../lib/format'
import {
  LISTING_TYPES,
  isPricedListingType,
  type ListingStatus,
  type ListingType,
  type Material,
  type MaterialSort,
  type MaterialUpdate,
  type Page,
} from '../../lib/types'

/*
 * The uploader's own catalogue.
 *
 * Browse deliberately hides everything that is not APPROVED, which leaves the
 * uploader with no way to tell "still queued" apart from "rejected, and here is
 * why". That is what this screen is for: asking for your own `uploader_id`
 * pulls PENDING, REJECTED and REMOVED back in, the rows are grouped so the
 * queue is legible at a glance, and a rejected listing shows the moderator's
 * note beside it rather than silently vanishing.
 */

const PAGE_SIZE = 24

/**
 * Shape sent to `GET /materials` -- same client-side query builder as browse.
 *
 * There is no `status` filter here on purpose. `search_materials` widens the
 * visibility clause by itself once `uploader_id` is the caller's own id, so
 * every PENDING, REJECTED and REMOVED row comes back; the `status` parameter
 * only ever *narrows* to one status, and it accepts a single `ListingStatus`,
 * never an "all" sentinel.
 */
interface MyListingsQuery {
  uploader_id: string
  sort: MaterialSort
  page: number
  page_size: number
}

/** Rejections first: that is the only group with something to act on. */
const GROUP_ORDER: readonly ListingStatus[] = [
  'REJECTED',
  'PENDING',
  'APPROVED',
  'RESERVED',
  'COMPLETED',
  'REMOVED',
]

const GROUP_BLURBS: Record<ListingStatus, string> = {
  REJECTED: 'A moderator turned these down. Fix what the note asks for and save — that resubmits it.',
  PENDING: 'Waiting on a moderator. These are not visible in browse yet.',
  APPROVED: 'Live in browse and open to requests.',
  RESERVED: 'Someone has an accepted request on these. Finish the handoff from the transaction.',
  COMPLETED: 'Handed over. Kept here for your record.',
  REMOVED: 'Pulled by a moderator, usually after a report.',
}

interface EditState {
  listing_id: string
  /** The status it had when the modal opened, so the warning can be specific. */
  status: ListingStatus
  title: string
  description: string
  course_code: string
  department: string
  semester: string
  edition: string
  listing_type: ListingType
  price: string
}

type EditField = 'title' | 'course_code' | 'department' | 'semester' | 'price'
type EditErrors = Partial<Record<EditField, string>>

function toEditState(listing: Material): EditState {
  return {
    listing_id: listing.listing_id,
    status: listing.status,
    title: listing.title,
    description: listing.description ?? '',
    course_code: listing.course_code,
    department: listing.department,
    semester: listing.semester,
    edition: listing.edition ?? '',
    listing_type: listing.listing_type,
    price: listing.price !== null && listing.price !== undefined ? String(listing.price) : '',
  }
}

// ---------------------------------------------------------------------------
// moderation feedback
// ---------------------------------------------------------------------------

/** The whole point of the screen: why a listing is not where the uploader put it. */
function ModerationNote({ listing }: { listing: Material }) {
  const rejected = listing.status === 'REJECTED'
  const note = listing.moderation_note?.trim()

  return (
    <div className="rounded-lg bg-state-rejected/5 p-3 ring-1 ring-inset ring-state-rejected/20">
      <p className="text-xs font-semibold uppercase tracking-wide text-state-rejected">
        {rejected ? 'Why it was rejected' : 'Why it was removed'}
      </p>
      <p className="mt-1 text-sm leading-relaxed text-slate-700">
        {note ??
          'No reason was recorded. Message a moderator, or edit the listing and resubmit it.'}
      </p>
      {rejected ? (
        <p className="mt-1.5 text-xs text-slate-500">
          Editing it sends it straight back to the moderation queue.
        </p>
      ) : null}
    </div>
  )
}

// ---------------------------------------------------------------------------
// page
// ---------------------------------------------------------------------------

export default function MyListingsPage() {
  const { user } = useAuth()
  const queryClient = useQueryClient()
  const toast = useToast()

  const [page, setPage] = useState(1)
  const [editing, setEditing] = useState<EditState | null>(null)
  const [editErrors, setEditErrors] = useState<EditErrors>({})
  const [deleting, setDeleting] = useState<Material | null>(null)
  /** A 409 is kept in the dialog rather than toasted; it names a live transaction. */
  const [deleteError, setDeleteError] = useState<unknown>(null)

  const uploaderId = user?.user_id ?? ''

  const query: MyListingsQuery = {
    uploader_id: uploaderId,
    sort: 'newest',
    page,
    page_size: PAGE_SIZE,
  }

  const listingsQuery = useQuery({
    queryKey: ['materials', 'mine', query],
    queryFn: () => api.get<Page<Material>>('/materials', { query }),
    enabled: Boolean(uploaderId),
    placeholderData: keepPreviousData,
  })

  const saveListing = useMutation({
    mutationFn: ({ listingId, payload }: { listingId: string; payload: MaterialUpdate }) =>
      api.patch<Material>(`/materials/${listingId}`, payload),
    onSuccess: async () => {
      setEditing(null)
      setEditErrors({})
      await queryClient.invalidateQueries({ queryKey: ['materials'] })
      toast.success('Listing updated', 'It goes back to a moderator before it is visible again.')
    },
    onError: (cause) => toast.fromError(cause, 'Could not save your changes'),
  })

  const deleteListing = useMutation({
    mutationFn: (listingId: string) => api.del<void>(`/materials/${listingId}`),
    onSuccess: async () => {
      setDeleting(null)
      setDeleteError(null)
      await queryClient.invalidateQueries({ queryKey: ['materials'] })
      toast.success('Listing deleted', 'Its file and preview were deleted with it.')
    },
    onError: (cause) => {
      // 409 means a transaction still references it. The server's message says
      // which one, so it belongs in front of the user, not in a toast that
      // disappears before it can be read.
      if (cause instanceof ApiError && cause.status === 409) {
        setDeleteError(cause)
        return
      }
      setDeleting(null)
      toast.fromError(cause, 'Could not delete the listing')
    },
  })

  const results = listingsQuery.data
  const items = results?.items ?? []

  const groups = GROUP_ORDER.map((status) => ({
    status,
    listings: items.filter((listing) => listing.status === status),
  })).filter((group) => group.listings.length > 0)

  // A newly deleted or edited listing must not leave a stale page number behind.
  useEffect(() => {
    if (results && page > results.pages && results.pages >= 1) setPage(results.pages)
  }, [results, page])

  const editPriced = editing ? isPricedListingType(editing.listing_type) : false

  const validateEdit = (state: EditState): EditErrors => {
    const next: EditErrors = {}
    if (state.title.trim().length < 3) next.title = 'Give it a title students will recognise.'
    if (state.course_code.trim().length < 2) next.course_code = 'Which course is this for?'
    if (!state.department.trim()) next.department = 'Department is required.'
    if (!state.semester.trim()) next.semester = 'Semester is required.'
    if (isPricedListingType(state.listing_type)) {
      const value = Number(state.price)
      if (state.price.trim() === '' || !Number.isFinite(value) || value < 0) {
        next.price = 'Enter a price of zero or more.'
      }
    }
    return next
  }

  const submitEdit = (event: FormEvent<HTMLFormElement>): void => {
    event.preventDefault()
    if (!editing) return

    const nextErrors = validateEdit(editing)
    setEditErrors(nextErrors)
    if (Object.keys(nextErrors).length > 0) return

    const priced = isPricedListingType(editing.listing_type)
    const payload: MaterialUpdate = {
      title: editing.title.trim(),
      description: editing.description.trim() || null,
      course_code: editing.course_code.trim(),
      department: editing.department.trim(),
      semester: editing.semester.trim(),
      edition: editing.edition.trim() || null,
      listing_type: editing.listing_type,
      // The CHECK constraint wants NULL on EXCHANGE and FREE, a number otherwise.
      price: priced ? Number(editing.price) : null,
    }
    saveListing.mutate({ listingId: editing.listing_id, payload })
  }

  const patchEdit = (next: Partial<EditState>): void => {
    setEditing((current) => (current ? { ...current, ...next } : current))
    setEditErrors({})
  }

  const closeEdit = (): void => {
    setEditing(null)
    setEditErrors({})
  }

  const closeDelete = (): void => {
    setDeleting(null)
    setDeleteError(null)
  }

  // ---------------------------------------------------------------- render
  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900">My listings</h1>
          <p className="mt-1 text-sm text-slate-500">
            Everything you have uploaded, including what a moderator has not looked at yet.
          </p>
        </div>
        <Link to="/upload" className="btn btn-primary">
          Upload material
        </Link>
      </header>

      {listingsQuery.isError ? (
        <ErrorBox
          error={listingsQuery.error}
          title="Could not load your listings"
          onRetry={() => void listingsQuery.refetch()}
        />
      ) : listingsQuery.isPending ? (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {Array.from({ length: 3 }, (_, index) => (
            <div key={index} className="card h-56 animate-pulse bg-slate-100" />
          ))}
        </div>
      ) : items.length === 0 ? (
        <EmptyState
          title="You have not listed anything yet"
          description="Upload your notes, textbooks or past papers. They appear here straight away, and in browse once a moderator approves them."
          action={
            <Link to="/upload" className="btn btn-primary">
              Upload material
            </Link>
          }
        />
      ) : (
        <>
          <p className="text-sm text-slate-500" aria-live="polite">
            {pluralize(results?.total ?? items.length, 'listing')} across every status.
          </p>

          <div className={cx('space-y-8', listingsQuery.isFetching && 'opacity-60 transition-opacity')}>
            {groups.map((group) => (
              <section key={group.status} className="space-y-3">
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                  <StatusPill status={group.status} kind="listing" />
                  <span className="text-sm text-slate-400">{group.listings.length}</span>
                  <p className="text-sm text-slate-500">{GROUP_BLURBS[group.status]}</p>
                </div>

                <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
                  {group.listings.map((listing) => (
                    <div key={listing.listing_id} className="flex flex-col gap-2">
                      <ListingCard
                        listing={listing}
                        showStatus
                        className="flex-1"
                        actions={
                          <>
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => {
                                setEditErrors({})
                                setEditing(toEditState(listing))
                              }}
                            >
                              Edit
                            </Button>
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => {
                                setDeleteError(null)
                                setDeleting(listing)
                              }}
                            >
                              Delete
                            </Button>
                          </>
                        }
                      />

                      {listing.status === 'REJECTED' || listing.status === 'REMOVED' ? (
                        <ModerationNote listing={listing} />
                      ) : null}
                    </div>
                  ))}
                </div>
              </section>
            ))}
          </div>

          <Pagination
            page={results?.page ?? page}
            pages={results?.pages ?? 1}
            total={results?.total}
            pageSize={results?.page_size ?? PAGE_SIZE}
            onPageChange={(nextPage) => {
              setPage(nextPage)
              window.scrollTo({ top: 0, behavior: 'smooth' })
            }}
          />
        </>
      )}

      {/* ------------------------------------------------------- edit modal */}
      <Modal
        open={editing !== null}
        onClose={closeEdit}
        title="Edit listing"
        description="Change the details students search on. The file itself cannot be swapped — delete and upload again for that."
        size="lg"
        dismissable={!saveListing.isPending}
        footer={
          <>
            <Button variant="ghost" onClick={closeEdit} disabled={saveListing.isPending}>
              Cancel
            </Button>
            <Button type="submit" form="edit-listing-form" loading={saveListing.isPending}>
              Save changes
            </Button>
          </>
        }
      >
        {editing ? (
          <form id="edit-listing-form" className="space-y-4" onSubmit={submitEdit} noValidate>
            {/* FR 1.1 -- editing metadata returns the listing to PENDING. */}
            <p className="rounded-lg bg-state-pending/5 px-3 py-2.5 text-sm leading-relaxed text-state-pending ring-1 ring-inset ring-state-pending/20">
              {editing.status === 'APPROVED' || editing.status === 'RESERVED'
                ? 'Saving takes this listing out of browse and sends it back to moderation until it is approved again.'
                : 'Saving puts this listing back at the end of the moderation queue.'}
            </p>

            <Input
              label="Title"
              required
              value={editing.title}
              error={editErrors.title}
              onChange={(event) => patchEdit({ title: event.target.value })}
            />

            <TextArea
              label="Description"
              rows={3}
              maxLength={2000}
              showCount
              value={editing.description}
              onChange={(event) => patchEdit({ description: event.target.value })}
            />

            <div className="grid gap-4 sm:grid-cols-2">
              <Input
                label="Course code"
                required
                value={editing.course_code}
                error={editErrors.course_code}
                onChange={(event) => patchEdit({ course_code: event.target.value })}
              />
              <Input
                label="Department"
                required
                value={editing.department}
                error={editErrors.department}
                onChange={(event) => patchEdit({ department: event.target.value })}
              />
              <Input
                label="Semester"
                required
                value={editing.semester}
                error={editErrors.semester}
                onChange={(event) => patchEdit({ semester: event.target.value })}
              />
              <Input
                label="Edition"
                value={editing.edition}
                onChange={(event) => patchEdit({ edition: event.target.value })}
              />
            </div>

            <fieldset>
              <legend className="label">Listing type</legend>
              <div className="grid gap-2 sm:grid-cols-4">
                {LISTING_TYPES.map((type) => {
                  const active = editing.listing_type === type
                  return (
                    <label
                      key={type}
                      className={cx(
                        'flex cursor-pointer items-center justify-center rounded-lg px-3 py-2 text-sm font-medium ring-1 ring-inset transition-colors',
                        active
                          ? listingTypeColor(type)
                          : 'bg-white text-slate-600 ring-slate-300 hover:bg-slate-50',
                      )}
                    >
                      <input
                        type="radio"
                        name="edit_listing_type"
                        value={type}
                        checked={active}
                        onChange={() =>
                          patchEdit({
                            listing_type: type,
                            // EXCHANGE and FREE must reach the server with no price.
                            price: isPricedListingType(type) ? editing.price : '',
                          })
                        }
                        className="sr-only"
                      />
                      {listingTypeLabel(type)}
                    </label>
                  )
                })}
              </div>
            </fieldset>

            {editPriced ? (
              <Input
                label={editing.listing_type === 'RENT' ? 'Rental price' : 'Asking price'}
                type="number"
                min={0}
                step="1"
                inputMode="numeric"
                required
                prefix="৳"
                value={editing.price}
                error={editErrors.price}
                onChange={(event) => patchEdit({ price: event.target.value })}
              />
            ) : (
              <p className="rounded-lg bg-slate-50 px-3 py-2.5 text-sm text-slate-600 ring-1 ring-inset ring-slate-200">
                {editing.listing_type === 'FREE'
                  ? 'Free listings carry no price.'
                  : 'Exchange listings carry no price — you agree the swap in chat.'}
              </p>
            )}
          </form>
        ) : null}
      </Modal>

      {/* ----------------------------------------------------- delete dialog */}
      <ConfirmDialog
        open={deleting !== null}
        title="Delete this listing?"
        danger
        confirmLabel="Delete listing"
        loading={deleteListing.isPending}
        onCancel={closeDelete}
        onConfirm={() => {
          if (deleting) deleteListing.mutate(deleting.listing_id)
        }}
        message={
          <>
            <span className="font-medium text-slate-900">{deleting?.title}</span> will be removed
            along with its stored file and preview. This cannot be undone.
          </>
        }
      >
        {deleteError ? <ErrorBox error={deleteError} title="This listing is in use" /> : null}
      </ConfirmDialog>
    </div>
  )
}
