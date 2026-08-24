import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { Button } from '../../components/Button'
import { EmptyState } from '../../components/EmptyState'
import { ErrorBox } from '../../components/ErrorBox'
import { Modal } from '../../components/Modal'
import { Pagination } from '../../components/Pagination'
import { Pill } from '../../components/Pill'
import { Select } from '../../components/Select'
import { PageSpinner } from '../../components/Spinner'
import { StatusPill } from '../../components/StatusPill'
import { TextArea } from '../../components/TextArea'
import { useToast } from '../../components/Toast'
import { api } from '../../lib/api'
import { cx, formatDateTime, pluralize, relativeTime, reportReasonLabel } from '../../lib/format'
import type { Page, Report, ReportReason, ReportResolution, ReportStatus } from '../../lib/types'

/**
 * FR 3.5 (admin half) + FR 4.1 -- the report queue.
 *
 * Filing a report is a student action; this is the other end of it. Resolving
 * upholds the report and may remove the listing in the same call; dismissing
 * records that the listing was looked at and found fine, which is what stops
 * the same listing being re-reviewed forever.
 */

const PAGE_SIZE = 15

type StatusFilter = ReportStatus | 'ALL'

const STATUS_OPTIONS: { value: StatusFilter; label: string }[] = [
  { value: 'OPEN', label: 'Awaiting review' },
  { value: 'RESOLVED', label: 'Resolved' },
  { value: 'DISMISSED', label: 'Dismissed' },
  { value: 'ALL', label: 'All reports' },
]

/**
 * Reason -> token classes. Kept as literals for the Tailwind scanner, and
 * separate from the status palette because a reason is not a state: it is the
 * reporter's claim, not the moderator's decision.
 */
const REASON_COLORS: Record<ReportReason, string> = {
  INAPPROPRIATE: 'bg-state-rejected/10 text-state-rejected ring-state-rejected/20',
  FAKE: 'bg-state-rejected/10 text-state-rejected ring-state-rejected/20',
  COPYRIGHT: 'bg-state-pending/10 text-state-pending ring-state-pending/20',
  SPAM: 'bg-state-pending/10 text-state-pending ring-state-pending/20',
  OTHER: 'bg-slate-100 text-slate-600 ring-slate-200',
}

/**
 * The resolve endpoint takes a verb, not a status: `RESOLVE` / `DISMISS`. The
 * report's own status (`RESOLVED` / `DISMISSED`) is what the server writes in
 * response to it, and is never posted.
 */
type ResolveAction = ReportResolution['action']

const ACTION_COPY: Record<ResolveAction, { title: string; body: string }> = {
  RESOLVE: {
    title: 'Resolve — the report was justified',
    body: 'Records that the listing broke a rule. Tick the box below if it should also come down.',
  },
  DISMISS: {
    title: 'Dismiss — nothing wrong here',
    body: 'The listing stays exactly as it is. Use this for mistaken or retaliatory reports.',
  },
}

const th = 'px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-slate-500'
const td = 'px-4 py-4 align-top text-sm text-slate-700'

// ---------------------------------------------------------------------------
// page
// ---------------------------------------------------------------------------

export default function ReportsPage() {
  const queryClient = useQueryClient()
  const toast = useToast()

  const [status, setStatus] = useState<StatusFilter>('OPEN')
  const [page, setPage] = useState(1)

  const [resolving, setResolving] = useState<Report | null>(null)
  const [action, setAction] = useState<ResolveAction>('RESOLVE')
  const [removeListing, setRemoveListing] = useState(false)
  const [note, setNote] = useState('')

  const reportsQuery = useQuery({
    queryKey: ['admin', 'reports', 'list', { status, page }],
    queryFn: () =>
      api.get<Page<Report>>('/admin/reports', {
        query: { status: status === 'ALL' ? undefined : status, page, page_size: PAGE_SIZE },
      }),
  })

  function closeModal(): void {
    setResolving(null)
    setAction('RESOLVE')
    setRemoveListing(false)
    setNote('')
  }

  const resolve = useMutation({
    mutationFn: ({ report, payload }: { report: Report; payload: ReportResolution }) =>
      api.post<unknown>(`/admin/reports/${report.report_id}/resolve`, payload),
    onSuccess: (_result, variables) => {
      toast.success(
        variables.payload.action === 'RESOLVE' ? 'Report resolved' : 'Report dismissed',
        variables.payload.remove_listing
          ? 'The listing has been removed and is no longer visible to students.'
          : 'The listing was left in place.',
      )
      closeModal()
      void queryClient.invalidateQueries({ queryKey: ['admin'] })
    },
    onError: (error) => toast.fromError(error, 'Could not close this report'),
  })

  function submit(): void {
    if (!resolving) return
    resolve.mutate({
      report: resolving,
      payload: {
        action,
        note: note.trim() || null,
        // Dismissing means "nothing wrong here", so it can never remove a listing.
        remove_listing: action === 'RESOLVE' ? removeListing : false,
      },
    })
  }

  const items = reportsQuery.data?.items ?? []
  const total = reportsQuery.data?.total ?? 0

  return (
    <div>
      <header className="mb-6 flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-slate-900">Reported listings</h1>
          <p className="mt-1 max-w-2xl text-sm text-slate-500">
            Every report names one listing and one reason. Resolving can take the listing down;
            dismissing leaves it untouched. Either way the report stops appearing in the open queue.
          </p>
        </div>
        <Select
          label="Status"
          containerClassName="w-full sm:w-56"
          value={status}
          onChange={(event) => {
            setStatus(event.target.value as StatusFilter)
            setPage(1)
          }}
          options={STATUS_OPTIONS}
        />
      </header>

      {reportsQuery.isError ? (
        <ErrorBox
          error={reportsQuery.error}
          title="Could not load reports"
          onRetry={() => void reportsQuery.refetch()}
        />
      ) : reportsQuery.isPending ? (
        <PageSpinner label="Loading reports" />
      ) : items.length === 0 ? (
        <EmptyState
          title={status === 'OPEN' ? 'No open reports' : 'Nothing matches this filter'}
          description={
            status === 'OPEN'
              ? 'Nobody has flagged a listing that is still waiting on a decision. Reports students file land here immediately.'
              : 'Try a different status — the queue may only contain reports that are still open.'
          }
          action={
            status === 'OPEN' ? (
              <Link to="/admin/moderation" className="btn btn-ghost">
                Go to the moderation queue
              </Link>
            ) : (
              <Button variant="ghost" onClick={() => setStatus('OPEN')}>
                Show open reports
              </Button>
            )
          }
        />
      ) : (
        <>
          <div className="card overflow-hidden">
            <div className="overflow-x-auto">
              <table className="w-full min-w-[56rem] border-collapse">
                <thead className="border-b border-slate-200 bg-slate-50">
                  <tr>
                    <th scope="col" className={th}>
                      Listing
                    </th>
                    <th scope="col" className={th}>
                      Reporter
                    </th>
                    <th scope="col" className={th}>
                      Reason
                    </th>
                    <th scope="col" className={th}>
                      Details
                    </th>
                    <th scope="col" className={th}>
                      Filed
                    </th>
                    <th scope="col" className={th}>
                      Status
                    </th>
                    <th scope="col" className={cx(th, 'text-right')}>
                      <span className="sr-only">Action</span>
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {items.map((report) => (
                    <tr key={report.report_id} className="transition-colors hover:bg-slate-50/70">
                      <td className={cx(td, 'max-w-xs')}>
                        <Link
                          to={`/materials/${report.listing_id}`}
                          className="font-medium text-slate-900 underline decoration-slate-300 underline-offset-2 hover:text-brand-700"
                        >
                          {report.listing_title ?? 'Untitled listing'}
                        </Link>
                      </td>

                      <td className={td}>
                        <Link
                          to={`/users/${report.reporter_id}`}
                          className="text-slate-700 hover:text-brand-700"
                        >
                          {report.reporter_name ?? 'A student'}
                        </Link>
                      </td>

                      <td className={td}>
                        <Pill color={REASON_COLORS[report.reason]}>
                          {reportReasonLabel(report.reason)}
                        </Pill>
                      </td>

                      <td className={cx(td, 'max-w-sm')}>
                        {report.details ? (
                          <p className="line-clamp-3 leading-relaxed text-slate-600">
                            {report.details}
                          </p>
                        ) : (
                          <span className="italic text-slate-400">No detail given</span>
                        )}
                        {report.status !== 'OPEN' && report.resolution_note ? (
                          <p className="mt-2 border-l-2 border-slate-200 pl-2 text-xs leading-relaxed text-slate-500">
                            Moderator note: {report.resolution_note}
                          </p>
                        ) : null}
                      </td>

                      <td className={cx(td, 'whitespace-nowrap')}>
                        <span title={formatDateTime(report.created_at)}>
                          {relativeTime(report.created_at)}
                        </span>
                      </td>

                      <td className={td}>
                        <StatusPill status={report.status} kind="report" />
                        {report.status !== 'OPEN' && report.resolved_at ? (
                          <p className="mt-1 whitespace-nowrap text-xs text-slate-400">
                            {relativeTime(report.resolved_at)}
                          </p>
                        ) : null}
                      </td>

                      <td className={cx(td, 'text-right')}>
                        {report.status === 'OPEN' ? (
                          <Button
                            size="sm"
                            onClick={() => {
                              setResolving(report)
                              setAction('RESOLVE')
                              setRemoveListing(false)
                              setNote('')
                            }}
                          >
                            Review
                          </Button>
                        ) : (
                          <span className="text-xs text-slate-400">Closed</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
            <p className="text-sm text-slate-500">{pluralize(total, 'report')} in this view</p>
            <Pagination
              page={page}
              pages={reportsQuery.data?.pages ?? 1}
              total={total}
              pageSize={PAGE_SIZE}
              onPageChange={setPage}
            />
          </div>
        </>
      )}

      <Modal
        open={Boolean(resolving)}
        onClose={closeModal}
        title="Close this report"
        description={resolving?.listing_title ?? undefined}
        size="md"
        footer={
          <>
            <Button variant="ghost" onClick={closeModal} disabled={resolve.isPending}>
              Cancel
            </Button>
            <Button
              variant={action === 'RESOLVE' && removeListing ? 'danger' : 'primary'}
              loading={resolve.isPending}
              onClick={submit}
            >
              {action === 'RESOLVE'
                ? removeListing
                  ? 'Resolve and remove listing'
                  : 'Resolve report'
                : 'Dismiss report'}
            </Button>
          </>
        }
      >
        {resolving ? (
          <div className="mb-5 rounded-lg bg-slate-50 p-3 text-sm ring-1 ring-inset ring-slate-200">
            <p className="font-medium text-slate-800">
              {reportReasonLabel(resolving.reason)} · reported {relativeTime(resolving.created_at)}
            </p>
            <p className="mt-1 leading-relaxed text-slate-600">
              {resolving.details || 'The reporter did not add any detail.'}
            </p>
          </div>
        ) : null}

        <fieldset>
          <legend className="label">What is the decision?</legend>
          <div className="space-y-2">
            {(['RESOLVE', 'DISMISS'] as const).map((value) => (
              <label
                key={value}
                className={cx(
                  'flex cursor-pointer gap-3 rounded-lg p-3 ring-1 ring-inset transition-colors',
                  action === value
                    ? 'bg-brand-50 ring-brand-600'
                    : 'bg-white ring-slate-200 hover:bg-slate-50',
                )}
              >
                <input
                  type="radio"
                  name="report-action"
                  value={value}
                  checked={action === value}
                  onChange={() => {
                    setAction(value)
                    if (value === 'DISMISS') setRemoveListing(false)
                  }}
                  className="mt-0.5 h-4 w-4 shrink-0 accent-brand-600"
                />
                <span className="min-w-0">
                  <span className="block text-sm font-medium text-slate-900">
                    {ACTION_COPY[value].title}
                  </span>
                  <span className="mt-0.5 block text-xs leading-relaxed text-slate-500">
                    {ACTION_COPY[value].body}
                  </span>
                </span>
              </label>
            ))}
          </div>
        </fieldset>

        <label
          className={cx(
            'mt-4 flex gap-3 rounded-lg p-3 ring-1 ring-inset transition-colors',
            action === 'DISMISS'
              ? 'cursor-not-allowed bg-slate-50 ring-slate-200 opacity-60'
              : 'cursor-pointer bg-white ring-slate-200 hover:bg-slate-50',
          )}
        >
          <input
            type="checkbox"
            checked={removeListing}
            disabled={action === 'DISMISS'}
            onChange={(event) => setRemoveListing(event.target.checked)}
            className="mt-0.5 h-4 w-4 shrink-0 rounded accent-brand-600"
          />
          <span className="min-w-0">
            <span className="block text-sm font-medium text-slate-900">
              Also remove the listing
            </span>
            <span className="mt-0.5 block text-xs leading-relaxed text-slate-500">
              {action === 'DISMISS'
                ? 'Not available when dismissing — a dismissed report leaves the listing alone.'
                : 'Sets the listing to Removed. It disappears from search and from the uploader’s public profile.'}
            </span>
          </span>
        </label>

        <TextArea
          containerClassName="mt-4"
          label="Resolution note"
          rows={3}
          maxLength={500}
          showCount
          value={note}
          hint="Optional, but it is the record of why this decision was made. Stored on the report."
          placeholder="e.g. Scan of a copyrighted textbook, removed under the takedown policy."
          onChange={(event) => setNote(event.target.value)}
        />
      </Modal>
    </div>
  )
}
