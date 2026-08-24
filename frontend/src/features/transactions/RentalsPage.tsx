import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { Button } from '../../components/Button'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import { EmptyState } from '../../components/EmptyState'
import { ErrorBox } from '../../components/ErrorBox'
import { Pill } from '../../components/Pill'
import { PageSpinner, Spinner } from '../../components/Spinner'
import { useToast } from '../../components/Toast'
import { ApiError, api } from '../../lib/api'
import { cx, formatDate, parseDate, pluralize, statusAccent, statusColor } from '../../lib/format'
import type { ListingStatus, Rental } from '../../lib/types'

/**
 * FR 3.2 -- rental due-date tracking.
 *
 * A rental row is created when a `RENT` transaction completes at handoff, and
 * the daily job walks the unreturned ones,
 * bucketing them into T-3, T-1 and overdue. This page is the at-a-glance
 * version of the same state, and the only place a rental is marked returned.
 *
 * The countdown is never computed from the browser clock: `days_remaining` and
 * `is_overdue` come off the wire, so the chip agrees with whatever the reminder
 * job saw.
 */

// ---------------------------------------------------------------------------
// the countdown chip
// ---------------------------------------------------------------------------

type Urgency = 'calm' | 'warning' | 'danger'

/**
 * The three chip states borrow the status tokens from tailwind.config.js, so
 * the colour class *literals* stay in format.ts where the JIT scanner sees
 * them -- no new palette, no hex in this file.
 */
const URGENCY_TOKEN: Record<Urgency, ListingStatus> = {
  calm: 'APPROVED',
  warning: 'PENDING',
  danger: 'REJECTED',
}

/** Lateness is decided server-side; `is_overdue` is the only source for it. */
function urgencyOf(rental: Rental): Urgency {
  if (rental.is_overdue) return 'danger'
  return rental.days_remaining <= 3 ? 'warning' : 'calm'
}

function remainingLabel(rental: Rental): string {
  if (rental.is_overdue) {
    const late = Math.abs(rental.days_remaining)
    return late === 0 ? 'Overdue' : `Overdue by ${pluralize(late, 'day')}`
  }
  if (rental.days_remaining === 0) return 'Due today'
  if (rental.days_remaining === 1) return 'Due tomorrow'
  return `${pluralize(rental.days_remaining, 'day')} left`
}

function Chevron({ open }: { open: boolean }) {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 20 20"
      fill="currentColor"
      className={cx('h-4 w-4 transition-transform', open && 'rotate-180')}
    >
      <path
        fillRule="evenodd"
        d="M5.22 8.22a.75.75 0 0 1 1.06 0L10 11.94l3.72-3.72a.75.75 0 1 1 1.06 1.06l-4.25 4.25a.75.75 0 0 1-1.06 0L5.22 9.28a.75.75 0 0 1 0-1.06Z"
        clipRule="evenodd"
      />
    </svg>
  )
}

// ---------------------------------------------------------------------------
// page
// ---------------------------------------------------------------------------

export default function RentalsPage() {
  const queryClient = useQueryClient()
  const toast = useToast()

  const [historyOpen, setHistoryOpen] = useState(false)
  const [confirming, setConfirming] = useState<Rental | null>(null)

  const activeQuery = useQuery({
    queryKey: ['rentals', 'me', { active_only: true }],
    queryFn: () => api.get<Rental[]>('/rentals/me', { query: { active_only: true } }),
  })

  const active = useMemo(
    () =>
      (activeQuery.data ?? [])
        .filter((rental) => !rental.returned)
        .sort((a, b) => a.days_remaining - b.days_remaining),
    [activeQuery.data],
  )

  /*
   * History costs a second request, so it is only fetched when the user opens
   * it -- or when nothing is active, where it decides which empty state is the
   * honest one: "you have never rented" or "everything is back".
   */
  const historyWanted = historyOpen || (activeQuery.isSuccess && active.length === 0)

  const historyQuery = useQuery({
    queryKey: ['rentals', 'me', { active_only: false }],
    queryFn: () => api.get<Rental[]>('/rentals/me', { query: { active_only: false } }),
    enabled: historyWanted,
  })

  const returned = useMemo(
    () =>
      (historyQuery.data ?? [])
        .filter((rental) => rental.returned)
        .sort(
          (a, b) =>
            (parseDate(b.returned_at)?.getTime() ?? 0) - (parseDate(a.returned_at)?.getTime() ?? 0),
        ),
    [historyQuery.data],
  )

  const markReturned = useMutation({
    mutationFn: (rentalId: string) => api.post<Rental>(`/rentals/${rentalId}/return`),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['rentals'] })
      // The transaction detail panel mirrors the rental's returned flag.
      void queryClient.invalidateQueries({ queryKey: ['transactions'] })
      toast.success('Marked as returned', 'Reminders for it stop from now on.')
    },
    onError: (error: unknown) => {
      // 409: the other side already closed it out, so our list is simply stale.
      if (error instanceof ApiError && error.status === 409) {
        void queryClient.invalidateQueries({ queryKey: ['rentals'] })
        toast.info('Already returned', error.detail)
        return
      }
      toast.fromError(error, 'Could not mark that rental returned')
    },
    onSettled: () => setConfirming(null),
  })

  const overdue = active.filter((rental) => rental.is_overdue)
  const neverRented = active.length === 0 && historyQuery.isSuccess && returned.length === 0

  return (
    <div className="space-y-8">
      <header>
        <h1 className="text-xl font-semibold tracking-tight text-slate-900">My rentals</h1>
        <p className="mt-1.5 max-w-2xl text-sm leading-relaxed text-slate-500">
          Everything you have on loan, with the time left on each one.
        </p>
        <p className="mt-2 max-w-2xl text-sm leading-relaxed text-slate-500">
          Reminders are automatic: NoteVault posts an in-app notification three days before a
          rental is due, again one day before, and once more if it goes overdue. They land in{' '}
          <Link to="/notifications" className="font-medium text-brand-700 hover:underline">
            your notifications
          </Link>{' '}
          — nothing is emailed, so it is worth checking back.
        </p>
      </header>

      {activeQuery.isPending ? (
        <PageSpinner label="Loading your rentals" />
      ) : activeQuery.isError ? (
        <ErrorBox
          error={activeQuery.error}
          onRetry={() => void activeQuery.refetch()}
          title="Your rentals could not be loaded"
        />
      ) : (
        <>
          {overdue.length > 0 ? (
            <div
              role="alert"
              className="rounded-xl bg-state-rejected/10 p-4 text-sm leading-relaxed text-slate-800 ring-1 ring-inset ring-state-rejected/20"
            >
              <span className="font-semibold">
                {pluralize(overdue.length, 'rental is', 'rentals are')} overdue.
              </span>{' '}
              Message the owner to arrange the handback, then mark it returned here.
            </div>
          ) : null}

          {/* ---- active ------------------------------------------------- */}
          <section className="space-y-3">
            <div className="flex items-baseline justify-between gap-3">
              <h2 className="text-sm font-semibold text-slate-900">Active rentals</h2>
              {active.length > 0 ? (
                <p className="text-xs text-slate-400">{pluralize(active.length, 'rental')}</p>
              ) : null}
            </div>

            {active.length === 0 ? (
              <EmptyState
                title={neverRented ? 'No rentals yet' : 'Nothing on loan right now'}
                description={
                  neverRented
                    ? 'A rental appears here the moment a “for rent” handoff completes, with its due date and a countdown.'
                    : 'Everything you borrowed is back with its owner. The returned list below keeps the record.'
                }
                action={
                  <Link to="/?listing_type=RENT" className="btn btn-primary">
                    Browse materials to rent
                  </Link>
                }
              />
            ) : (
              <ul className="space-y-3">
                {active.map((rental) => {
                  const urgency = urgencyOf(rental)
                  const token = URGENCY_TOKEN[urgency]

                  return (
                    <li key={rental.rental_id} className="card p-4">
                      <div className="flex flex-wrap items-start justify-between gap-4">
                        <div className="min-w-0 flex-1">
                          <h3 className="truncate text-sm font-semibold text-slate-900">
                            <Link
                              to={`/transactions/${rental.transaction_id}`}
                              className="hover:text-brand-700"
                            >
                              {rental.listing_title ?? 'Rented material'}
                            </Link>
                          </h3>

                          <p className="mt-1 text-xs text-slate-500">
                            Started {formatDate(rental.start_date)} · Due{' '}
                            <span className="font-medium text-slate-700">
                              {formatDate(rental.due_date)}
                            </span>
                          </p>
                        </div>

                        <div className="flex shrink-0 flex-col items-end gap-2">
                          <Pill
                            color={statusColor(token)}
                            dot={statusAccent(token)}
                            className="px-3 py-1 text-sm font-semibold"
                          >
                            {remainingLabel(rental)}
                          </Pill>

                          <div className="flex gap-2">
                            <Link
                              to={`/transactions/${rental.transaction_id}`}
                              className="btn btn-ghost px-2.5 py-1.5 text-xs"
                            >
                              Transaction
                            </Link>
                            <Button
                              size="sm"
                              loading={
                                markReturned.isPending &&
                                markReturned.variables === rental.rental_id
                              }
                              disabled={markReturned.isPending}
                              onClick={() => setConfirming(rental)}
                            >
                              Mark returned
                            </Button>
                          </div>
                        </div>
                      </div>
                    </li>
                  )
                })}
              </ul>
            )}
          </section>

          {/* ---- history (secondary, collapsed) -------------------------- */}
          {neverRented ? null : (
            <section className="space-y-3">
              <button
                type="button"
                onClick={() => setHistoryOpen((open) => !open)}
                aria-expanded={historyOpen}
                aria-controls="returned-rentals"
                className="flex w-full items-center justify-between gap-3 rounded-lg py-1 text-left text-slate-500 transition-colors hover:text-slate-800"
              >
                <span className="text-sm font-semibold text-slate-700">Returned</span>
                <span className="flex items-center gap-2 text-xs">
                  {historyQuery.isSuccess ? pluralize(returned.length, 'rental') : 'History'}
                  <Chevron open={historyOpen} />
                </span>
              </button>

              {historyOpen ? (
                <div id="returned-rentals">
                  {historyQuery.isPending ? (
                    <p className="flex items-center gap-2 px-1 text-sm text-slate-500">
                      <Spinner size="sm" label={null} />
                      Loading your returned rentals
                    </p>
                  ) : historyQuery.isError ? (
                    <ErrorBox
                      error={historyQuery.error}
                      onRetry={() => void historyQuery.refetch()}
                      title="The returned list could not be loaded"
                    />
                  ) : returned.length === 0 ? (
                    <p className="card px-4 py-6 text-center text-sm text-slate-500">
                      Nothing returned yet. Closed loans stay here as a record.
                    </p>
                  ) : (
                    <ul className="card divide-y divide-slate-100 overflow-hidden">
                      {returned.map((rental) => (
                        <li
                          key={rental.rental_id}
                          className="flex flex-wrap items-center justify-between gap-3 px-4 py-3"
                        >
                          <div className="min-w-0">
                            <Link
                              to={`/transactions/${rental.transaction_id}`}
                              className="block truncate text-sm font-medium text-slate-800 hover:text-brand-700"
                            >
                              {rental.listing_title ?? 'Rented material'}
                            </Link>
                            <p className="mt-0.5 text-xs text-slate-500">
                              {formatDate(rental.start_date)} – {formatDate(rental.due_date)}
                            </p>
                          </div>
                          <Pill color={statusColor('COMPLETED')} dot={statusAccent('COMPLETED')}>
                            Returned {formatDate(rental.returned_at)}
                          </Pill>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              ) : null}
            </section>
          )}
        </>
      )}

      <ConfirmDialog
        open={confirming !== null}
        title="Mark this rental as returned?"
        message={
          confirming ? (
            <>
              Confirm that{' '}
              <span className="font-medium text-slate-900">
                {confirming.listing_title ?? 'this material'}
              </span>{' '}
              is back with its owner. The countdown and its reminders stop immediately.
            </>
          ) : (
            ''
          )
        }
        confirmLabel="Yes, it is returned"
        loading={markReturned.isPending}
        onCancel={() => setConfirming(null)}
        onConfirm={() => {
          if (confirming) markReturned.mutate(confirming.rental_id)
        }}
      />
    </div>
  )
}
