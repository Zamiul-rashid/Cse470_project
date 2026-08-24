import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { Button } from '../../components/Button'
import { EmptyState } from '../../components/EmptyState'
import { ErrorBox } from '../../components/ErrorBox'
import { Modal } from '../../components/Modal'
import { Pagination } from '../../components/Pagination'
import { ListingTypePill, Pill } from '../../components/Pill'
import { PageSpinner } from '../../components/Spinner'
import { StatusPill } from '../../components/StatusPill'
import { TextArea } from '../../components/TextArea'
import { useToast } from '../../components/Toast'
import { api } from '../../lib/api'
import { formatDateTime, formatPrice, pluralize, relativeTime } from '../../lib/format'
import type { ApprovalResult, Material, ModerationDecision, Page } from '../../lib/types'

/**
 * FR 4.1 -- the moderation queue.
 *
 * The queue is the gate every listing passes through: a PENDING listing is
 * invisible to students, and approving it is what fires the auto-match job
 * (FR 2.3). Both decisions simply invalidate the query afterwards -- an
 * optimistic removal would hide the row before the server had committed the
 * transition, and a failed approve would then need the card put back.
 */

const PAGE_SIZE = 10

// ---------------------------------------------------------------------------
// card pieces
// ---------------------------------------------------------------------------

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs font-medium uppercase tracking-wide text-slate-400">{label}</dt>
      <dd className="mt-0.5 text-sm text-slate-800">{value}</dd>
    </div>
  )
}

// ---------------------------------------------------------------------------
// page
// ---------------------------------------------------------------------------

export default function ModerationPage() {
  const queryClient = useQueryClient()
  const toast = useToast()

  const [page, setPage] = useState(1)
  const [rejecting, setRejecting] = useState<Material | null>(null)
  const [note, setNote] = useState('')
  const [noteError, setNoteError] = useState<string | null>(null)

  const queueQuery = useQuery({
    queryKey: ['admin', 'moderation', 'queue', { page }],
    // Oldest first is the server's ordering -- the longest wait is the most urgent.
    queryFn: () =>
      api.get<Page<Material>>('/admin/moderation/queue', {
        query: { page, page_size: PAGE_SIZE },
      }),
  })

  const invalidateAdmin = (): void => {
    void queryClient.invalidateQueries({ queryKey: ['admin'] })
  }

  const approve = useMutation({
    mutationFn: (item: Material) =>
      api.post<ApprovalResult>(`/admin/materials/${item.listing_id}/approve`, {
        note: null,
      } satisfies ModerationDecision),
    onSuccess: (result) => {
      // `notified` is how many students the auto-match job told (FR 2.3).
      const notified = result.notified
      toast.success(
        notified > 0 ? `Approved — ${pluralize(notified, 'student')} notified` : 'Approved',
        notified > 0
          ? 'Everyone with an open request for this course has been told the listing is live.'
          : 'The listing is live. No open requests matched it.',
      )
      invalidateAdmin()
    },
    onError: (error) => toast.fromError(error, 'Could not approve this listing'),
  })

  const reject = useMutation({
    mutationFn: ({ item, reason }: { item: Material; reason: string }) =>
      api.post<unknown>(`/admin/materials/${item.listing_id}/reject`, {
        note: reason,
      } satisfies ModerationDecision),
    onSuccess: () => {
      toast.success('Rejected', 'The uploader has been sent your note.')
      closeReject()
      invalidateAdmin()
    },
    onError: (error) => toast.fromError(error, 'Could not reject this listing'),
  })

  function closeReject(): void {
    setRejecting(null)
    setNote('')
    setNoteError(null)
  }

  function submitReject(): void {
    if (!rejecting) return
    const reason = note.trim()
    // FR 4.1: a rejection without a reason is the one thing the API refuses.
    if (!reason) {
      setNoteError('A rejection needs a note — it is the only feedback the uploader gets.')
      return
    }
    reject.mutate({ item: rejecting, reason })
  }

  const items = queueQuery.data?.items ?? []
  const total = queueQuery.data?.total ?? 0

  return (
    <div className="mx-auto max-w-4xl">
      <header className="mb-6">
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-xl font-semibold tracking-tight text-slate-900">Moderation queue</h1>
          {queueQuery.isSuccess ? (
            <Pill color="bg-state-pending/10 text-state-pending ring-state-pending/20">
              {pluralize(total, 'listing')} waiting
            </Pill>
          ) : null}
        </div>
        <p className="mt-1 text-sm text-slate-500">
          Oldest submission first. Nothing here is visible to students yet — approving publishes the
          listing and notifies every open request for that course.
        </p>
      </header>

      {queueQuery.isError ? (
        <ErrorBox
          error={queueQuery.error}
          title="Could not load the moderation queue"
          onRetry={() => void queueQuery.refetch()}
        />
      ) : queueQuery.isPending ? (
        <PageSpinner label="Loading the queue" />
      ) : items.length === 0 ? (
        <EmptyState
          title="The queue is clear"
          description="Every submitted listing has been reviewed. New uploads land here the moment a student submits one."
          action={
            <Link to="/admin" className="btn btn-ghost">
              Back to the admin panel
            </Link>
          }
        />
      ) : (
        <>
          <ul className="space-y-4">
            {items.map((item) => {
              const busy =
                (approve.isPending && approve.variables?.listing_id === item.listing_id) ||
                (reject.isPending && reject.variables?.item.listing_id === item.listing_id)

              return (
                <li key={item.listing_id} className="card p-5">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0">
                      <h2 className="text-base font-semibold leading-snug text-slate-900">
                        {item.title}
                      </h2>
                      <p className="mt-1 text-sm text-slate-500">
                        Submitted {relativeTime(item.upload_date)} ·{' '}
                        {formatDateTime(item.upload_date)}
                      </p>
                    </div>
                    <div className="flex shrink-0 flex-wrap items-center gap-2">
                      <ListingTypePill type={item.listing_type} />
                      <StatusPill status={item.status} kind="listing" />
                    </div>
                  </div>

                  <dl className="mt-4 grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-4">
                    <Meta label="Course" value={item.course_code} />
                    <Meta label="Department" value={item.department} />
                    <Meta label="Semester" value={item.semester} />
                    <Meta label="Edition" value={item.edition ?? '—'} />
                    <Meta label="Price" value={formatPrice(item.listing_type, item.price)} />
                    <Meta label="Campus" value={item.campus_name ?? item.campus_id} />
                    <Meta
                      label="Pages"
                      value={item.page_count ? String(item.page_count) : 'Unknown'}
                    />
                    <Meta
                      label="Preview"
                      value={item.has_preview ? '3-page extract ready' : 'None generated'}
                    />
                  </dl>

                  {item.description ? (
                    <p className="mt-4 whitespace-pre-line border-l-2 border-slate-200 pl-3 text-sm leading-relaxed text-slate-600">
                      {item.description}
                    </p>
                  ) : (
                    <p className="mt-4 text-sm italic text-slate-400">
                      The uploader left no description.
                    </p>
                  )}

                  <div className="mt-5 flex flex-wrap items-center justify-between gap-3 border-t border-slate-100 pt-4">
                    <p className="text-sm text-slate-500">
                      Uploaded by{' '}
                      <Link
                        to={`/users/${item.uploader_id}`}
                        className="font-medium text-slate-800 underline decoration-slate-300 underline-offset-2 hover:text-brand-700"
                      >
                        {item.uploader_name ?? 'this student'}
                      </Link>
                    </p>

                    <div className="flex flex-wrap items-center gap-2">
                      {/*
                        The preview is a streamed, authenticated endpoint, so it
                        cannot be a bare href -- the listing page renders it with
                        the bearer token attached.
                      */}
                      <Link to={`/materials/${item.listing_id}`} className="btn btn-ghost">
                        Open preview
                      </Link>
                      <Button
                        variant="ghost"
                        disabled={busy}
                        onClick={() => {
                          setRejecting(item)
                          setNote('')
                          setNoteError(null)
                        }}
                      >
                        Reject
                      </Button>
                      <Button
                        loading={approve.isPending && approve.variables?.listing_id === item.listing_id}
                        disabled={busy}
                        onClick={() => approve.mutate(item)}
                      >
                        Approve
                      </Button>
                    </div>
                  </div>
                </li>
              )
            })}
          </ul>

          <Pagination
            className="mt-6"
            page={page}
            pages={queueQuery.data?.pages ?? 1}
            total={total}
            pageSize={PAGE_SIZE}
            onPageChange={setPage}
          />
        </>
      )}

      <Modal
        open={Boolean(rejecting)}
        onClose={closeReject}
        title="Reject this listing"
        description={rejecting?.title}
        size="md"
        footer={
          <>
            <Button variant="ghost" onClick={closeReject} disabled={reject.isPending}>
              Cancel
            </Button>
            <Button variant="danger" loading={reject.isPending} onClick={submitReject}>
              Reject listing
            </Button>
          </>
        }
      >
        <TextArea
          label="Why is it being rejected?"
          required
          rows={4}
          maxLength={500}
          showCount
          value={note}
          error={noteError}
          hint="The uploader sees this note verbatim. Name the specific problem — wrong course code, unreadable scan, someone else’s copyrighted material."
          placeholder="e.g. Pages 4-9 are unreadable — please rescan and submit again."
          onChange={(event) => {
            setNote(event.target.value)
            if (noteError) setNoteError(null)
          }}
        />
        <p className="mt-3 text-xs text-slate-500">
          The listing moves to Rejected and stays out of search. The uploader can edit and resubmit,
          which puts it back at the end of this queue.
        </p>
      </Modal>
    </div>
  )
}
