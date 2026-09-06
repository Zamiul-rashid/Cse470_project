import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'

import { EmptyState } from '../../components/EmptyState'
import { ErrorBox } from '../../components/ErrorBox'
import { Pill } from '../../components/Pill'
import { Select } from '../../components/Select'
import { PageSpinner } from '../../components/Spinner'
import { api } from '../../lib/api'
import { useAuth, useCampusFilter } from '../../lib/auth'
import { cx } from '../../lib/format'
import type { AnalyticsQuery, Campus, LeaderboardEntry, Page } from '../../lib/types'

/**
 * FR 4.2 -- the top contributor leaderboard.
 *
 * The campus selector writes to the shared header scope (FR 4.3) rather than
 * keeping a second copy of the filter, so moving between browse, trending and
 * this page keeps one answer to "which campus am I looking at?".
 */

const LIMIT = 25

/**
 * Analytics routes return a bare ranked array; the paged envelope is accepted
 * too so this page does not break if the route is later paginated.
 */
function toItems<T>(data: T[] | Page<T> | undefined): T[] {
  if (!data) return []
  return Array.isArray(data) ? data : data.items
}

/** Rank chip. The top three get the brand ramp; everyone else stays neutral. */
const RANK_STYLES: Record<number, string> = {
  1: 'bg-brand-600 text-white ring-brand-600',
  2: 'bg-brand-200 text-brand-900 ring-brand-300',
  3: 'bg-brand-50 text-brand-700 ring-brand-200',
}

function rankStyle(rank: number): string {
  return RANK_STYLES[rank] ?? 'bg-white text-slate-500 ring-slate-200'
}

const th = 'px-4 py-3 text-xs font-semibold uppercase tracking-wide text-slate-500'
const td = 'px-4 py-3 text-sm text-slate-700'

export default function LeaderboardPage() {
  const { user } = useAuth()
  const { campusId, setCampusId } = useCampusFilter()

  const campusesQuery = useQuery({
    queryKey: ['campuses'],
    queryFn: () => api.get<Campus[]>('/campuses'),
    staleTime: 60 * 60 * 1000,
  })

  const leaderboardQuery = useQuery({
    queryKey: ['analytics', 'leaderboard', { campus_id: campusId, limit: LIMIT }],
    queryFn: () =>
      api.get<LeaderboardEntry[] | Page<LeaderboardEntry>>('/analytics/leaderboard', {
        query: {
          campus_id: campusId ?? undefined,
          limit: LIMIT,
        } satisfies AnalyticsQuery,
      }),
    // The server caches this for five minutes; there is no point asking sooner.
    staleTime: 5 * 60 * 1000,
  })

  const entries = toItems(leaderboardQuery.data)
  const campusName = campusesQuery.data?.find((c) => c.campus_id === campusId)?.name

  return (
    <div className="mx-auto max-w-5xl">
      <header className="mb-6 flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-slate-900">Top contributors</h1>
          <p className="mt-1 max-w-2xl text-sm text-slate-500">
            The students keeping the catalogue stocked{campusName ? ` at ${campusName}` : ' across every campus'}.
          </p>
        </div>
        <Select
          label="Campus"
          containerClassName="w-full sm:w-64"
          value={campusId ?? ''}
          onChange={(event) => setCampusId(event.target.value || null)}
        >
          <option value="">All campuses</option>
          {(campusesQuery.data ?? []).map((campus) => (
            <option key={campus.campus_id} value={campus.campus_id}>
              {campus.name}
            </option>
          ))}
        </Select>
      </header>

      {/* The number is only meaningful if the reader can see how it is built. */}
      <p className="mb-4 rounded-lg bg-brand-50 px-4 py-3 text-sm leading-relaxed text-brand-900 ring-1 ring-inset ring-brand-100">
        <span className="font-semibold">Score</span> = 2 × approved uploads + 3 × completed
        exchanges + 5 × whatever the rating is above 3.0. Sustained contribution outranks a single
        five-star review, and an unrated newcomer is never penalised.
      </p>

      {leaderboardQuery.isError ? (
        <ErrorBox
          error={leaderboardQuery.error}
          title="Could not load the leaderboard"
          onRetry={() => void leaderboardQuery.refetch()}
        />
      ) : leaderboardQuery.isPending ? (
        <PageSpinner label="Ranking contributors" />
      ) : entries.length === 0 ? (
        <EmptyState
          title="No contributors yet"
          description={
            campusId
              ? 'Nobody on this campus has an approved upload or a completed exchange yet. Try widening the filter to all campuses.'
              : 'The leaderboard fills up once listings start getting approved and exchanges start completing.'
          }
          action={
            <Link to="/upload" className="btn btn-primary">
              Upload something
            </Link>
          }
        />
      ) : (
        <div className="card overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[46rem] border-collapse">
              <caption className="sr-only">
                Top contributors ranked by upload, exchange and rating score
              </caption>
              <thead className="border-b border-slate-200 bg-slate-50">
                <tr>
                  <th scope="col" className={cx(th, 'w-16 text-left')}>
                    Rank
                  </th>
                  <th scope="col" className={cx(th, 'text-left')}>
                    Contributor
                  </th>
                  <th scope="col" className={cx(th, 'text-left')}>
                    Campus
                  </th>
                  <th scope="col" className={cx(th, 'text-right')}>
                    Approved uploads
                  </th>
                  <th scope="col" className={cx(th, 'text-right')}>
                    Completed exchanges
                  </th>
                  <th scope="col" className={cx(th, 'text-right')}>
                    Rating
                  </th>
                  <th scope="col" className={cx(th, 'text-right')}>
                    Score
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {entries.map((entry) => {
                  const isTopThree = entry.rank <= 3
                  const isMe = user?.user_id === entry.user_id

                  return (
                    <tr
                      key={entry.user_id}
                      className={cx(
                        'transition-colors',
                        isTopThree ? 'bg-brand-50/50' : 'hover:bg-slate-50/70',
                      )}
                    >
                      <td className={td}>
                        <span
                          className={cx(
                            'inline-flex h-7 w-7 items-center justify-center rounded-full text-xs font-semibold tabular-nums ring-1 ring-inset',
                            rankStyle(entry.rank),
                          )}
                        >
                          {entry.rank}
                        </span>
                      </td>

                      <td className={td}>
                        <div className="flex flex-wrap items-center gap-2">
                          <Link
                            to={`/users/${entry.user_id}`}
                            className={cx(
                              'underline decoration-slate-300 underline-offset-2 hover:text-brand-700',
                              isTopThree
                                ? 'font-semibold text-slate-900'
                                : 'font-medium text-slate-800',
                            )}
                          >
                            {entry.name}
                          </Link>
                          {isMe ? (
                            <Pill color="bg-brand-100 text-brand-800 ring-brand-200">You</Pill>
                          ) : null}
                        </div>
                      </td>

                      {/* The entry carries the campus *name* only; the id is a
                          query parameter on the way in, never a column. */}
                      <td className={cx(td, 'text-slate-500')}>{entry.campus_name ?? '—'}</td>

                      <td className={cx(td, 'text-right tabular-nums')}>
                        {entry.approved_uploads}
                      </td>

                      <td className={cx(td, 'text-right tabular-nums')}>
                        {entry.completed_transactions}
                      </td>

                      <td className={cx(td, 'text-right tabular-nums')}>
                        {entry.rating_avg > 0 ? (
                          <span className="inline-flex items-center gap-1">
                            <svg
                              aria-hidden="true"
                              viewBox="0 0 20 20"
                              fill="currentColor"
                              className="h-3.5 w-3.5 text-state-pending"
                            >
                              <path d="M10 1.5l2.47 5.01 5.53.8-4 3.9.94 5.5L10 14.13 5.06 16.7l.94-5.5-4-3.9 5.53-.8L10 1.5Z" />
                            </svg>
                            {entry.rating_avg.toFixed(1)}
                          </span>
                        ) : (
                          <span className="text-slate-400">Not rated</span>
                        )}
                      </td>

                      <td
                        className={cx(
                          td,
                          'text-right font-semibold tabular-nums',
                          isTopThree ? 'text-brand-700' : 'text-slate-900',
                        )}
                      >
                        {Math.round(entry.score)}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}
