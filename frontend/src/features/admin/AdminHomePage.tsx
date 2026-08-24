import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'

import { ErrorBox } from '../../components/ErrorBox'
import { Spinner } from '../../components/Spinner'
import { api } from '../../lib/api'
import { cx, pluralize } from '../../lib/format'
import type { Material, Page, Report } from '../../lib/types'

/**
 * FR 4.1 -- the admin landing page.
 *
 * Both counters come from the real queues rather than a separate stats route:
 * asking for a single row and reading `Page.total` is one cheap query each, and
 * it can never disagree with what the queue pages themselves show.
 */
const COUNT_QUERY = { page: 1, page_size: 1 } as const

/** Shared prefix with the queue pages, so invalidating `['admin']` refreshes both. */
const adminKeys = {
  pendingCount: ['admin', 'moderation', 'count'] as const,
  openReportCount: ['admin', 'reports', 'count'] as const,
}

// ---------------------------------------------------------------------------
// counter card
// ---------------------------------------------------------------------------

type CounterTone = 'pending' | 'reports'

/** Token classes held as literals so the Tailwind scanner sees them. */
const TONES: Record<CounterTone, { value: string; badge: string }> = {
  pending: {
    value: 'text-state-pending',
    badge: 'bg-state-pending/10 text-state-pending',
  },
  reports: {
    value: 'text-state-rejected',
    badge: 'bg-state-rejected/10 text-state-rejected',
  },
}

const ICONS: Record<CounterTone, ReactNode> = {
  pending: (
    <svg aria-hidden="true" viewBox="0 0 20 20" fill="currentColor" className="h-5 w-5">
      <path
        fillRule="evenodd"
        d="M10 1.75a8.25 8.25 0 1 0 0 16.5 8.25 8.25 0 0 0 0-16.5ZM10.75 6a.75.75 0 0 0-1.5 0v4c0 .27.144.518.378.651l2.75 1.571a.75.75 0 1 0 .744-1.302L10.75 9.565V6Z"
        clipRule="evenodd"
      />
    </svg>
  ),
  reports: (
    <svg aria-hidden="true" viewBox="0 0 20 20" fill="currentColor" className="h-5 w-5">
      <path
        fillRule="evenodd"
        d="M3 2.75a.75.75 0 0 1 .75-.75h11.5a.75.75 0 0 1 .6 1.2L13.19 7l2.66 3.8a.75.75 0 0 1-.6 1.2H4.5v5.25a.75.75 0 0 1-1.5 0V2.75Z"
        clipRule="evenodd"
      />
    </svg>
  ),
}

interface CounterCardProps {
  to: string
  label: string
  unit: string
  caption: string
  cta: string
  tone: CounterTone
  /** `Page.total` off the queue query; undefined until it settles. */
  total: number | undefined
  loading: boolean
  failed: boolean
}

function CounterCard({
  to,
  label,
  unit,
  caption,
  cta,
  tone,
  total,
  loading,
  failed,
}: CounterCardProps) {
  const styles = TONES[tone]

  return (
    <Link
      to={to}
      className="card group flex flex-col gap-5 p-6 transition-shadow hover:shadow-pop focus-visible:shadow-pop"
    >
      <div className="flex items-start justify-between gap-4">
        <h2 className="text-sm font-semibold text-slate-700">{label}</h2>
        <span
          className={cx(
            'flex h-9 w-9 shrink-0 items-center justify-center rounded-full',
            styles.badge,
          )}
        >
          {ICONS[tone]}
        </span>
      </div>

      <div className="flex items-baseline gap-2">
        {loading ? (
          <span className="flex h-12 items-center text-slate-300">
            <Spinner size="lg" label={`Counting ${unit}s`} />
          </span>
        ) : failed ? (
          <span className="text-2xl font-semibold text-slate-300">—</span>
        ) : (
          <>
            <span className={cx('text-5xl font-semibold leading-none tabular-nums', styles.value)}>
              {total ?? 0}
            </span>
            <span className="text-sm text-slate-500">{total === 1 ? unit : `${unit}s`}</span>
          </>
        )}
      </div>

      <p className="text-sm leading-relaxed text-slate-500">{caption}</p>

      <span className="mt-auto inline-flex items-center gap-1.5 text-sm font-medium text-brand-700">
        {cta}
        <svg
          aria-hidden="true"
          viewBox="0 0 20 20"
          fill="currentColor"
          className="h-4 w-4 transition-transform group-hover:translate-x-0.5"
        >
          <path
            fillRule="evenodd"
            d="M7.22 4.22a.75.75 0 0 1 1.06 0l5.25 5.25a.75.75 0 0 1 0 1.06l-5.25 5.25a.75.75 0 1 1-1.06-1.06L11.94 10 7.22 5.28a.75.75 0 0 1 0-1.06Z"
            clipRule="evenodd"
          />
        </svg>
      </span>
    </Link>
  )
}

// ---------------------------------------------------------------------------
// page
// ---------------------------------------------------------------------------

const MODERATION_NOTES: { title: string; body: string }[] = [
  {
    title: 'Nothing is public until you approve it',
    body: 'A student’s upload is created as Pending review. It stays out of search, off the browse grid and unreachable by link until it clears this queue.',
  },
  {
    title: 'Approving notifies the people waiting',
    body: 'Approval runs the auto-match job: every open request for that course code gets a notification pointing at the new listing. That is why approvals matter more than they look.',
  },
  {
    title: 'Rejecting needs a reason',
    body: 'The note you write is the only feedback the uploader receives, so name the specific problem — wrong course, unreadable scan, someone else’s copyrighted book.',
  },
  {
    title: 'Reports run the other direction',
    body: 'A listing that was fine at review time can turn out to be fake, spam or a copyright violation. Resolving a report can remove the listing in the same step.',
  },
]

export default function AdminHomePage() {
  const pendingQuery = useQuery({
    queryKey: adminKeys.pendingCount,
    queryFn: () =>
      api.get<Page<Material>>('/admin/moderation/queue', { query: COUNT_QUERY }),
  })

  const reportsQuery = useQuery({
    queryKey: adminKeys.openReportCount,
    queryFn: () =>
      api.get<Page<Report>>('/admin/reports', { query: { ...COUNT_QUERY, status: 'OPEN' } }),
  })

  const loadError = pendingQuery.error ?? reportsQuery.error
  const totalOutstanding = (pendingQuery.data?.total ?? 0) + (reportsQuery.data?.total ?? 0)
  const settled = pendingQuery.isSuccess && reportsQuery.isSuccess

  return (
    <div className="mx-auto max-w-4xl">
      <header className="mb-6">
        <h1 className="text-xl font-semibold tracking-tight text-slate-900">Admin panel</h1>
        <p className="mt-1 text-sm text-slate-500">
          {settled && totalOutstanding === 0
            ? 'Both queues are clear. Nothing is waiting on you.'
            : `Two queues need a decision from a moderator${
                settled ? ` — ${pluralize(totalOutstanding, 'item')} outstanding` : ''
              }.`}
        </p>
      </header>

      {loadError ? (
        <ErrorBox
          error={loadError}
          title="Could not load the queue counts"
          onRetry={() => {
            void pendingQuery.refetch()
            void reportsQuery.refetch()
          }}
          className="mb-6"
        />
      ) : null}

      <div className="grid gap-4 sm:grid-cols-2">
        <CounterCard
          to="/admin/moderation"
          label="Listings awaiting review"
          unit="listing"
          caption="Uploads sitting at Pending review, oldest first. Approve to publish and notify matching requests, or reject with a note."
          cta="Open the moderation queue"
          tone="pending"
          total={pendingQuery.data?.total}
          loading={pendingQuery.isPending}
          failed={pendingQuery.isError}
        />
        <CounterCard
          to="/admin/reports"
          label="Open reports"
          unit="report"
          caption="Listings a student has flagged as inappropriate, fake, copyrighted or spam. Resolve or dismiss each one."
          cta="Open the report queue"
          tone="reports"
          total={reportsQuery.data?.total}
          loading={reportsQuery.isPending}
          failed={reportsQuery.isError}
        />
      </div>

      <section className="card mt-6 p-6">
        <h2 className="text-sm font-semibold text-slate-900">What moderation does</h2>
        <p className="mt-1 text-sm text-slate-500">
          Four things worth knowing before you start clicking Approve.
        </p>
        <dl className="mt-5 grid gap-5 sm:grid-cols-2">
          {MODERATION_NOTES.map((note) => (
            <div key={note.title}>
              <dt className="text-sm font-medium text-slate-800">{note.title}</dt>
              <dd className="mt-1 text-sm leading-relaxed text-slate-500">{note.body}</dd>
            </div>
          ))}
        </dl>
      </section>
    </div>
  )
}
