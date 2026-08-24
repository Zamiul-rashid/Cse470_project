import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'

import { EmptyState } from '../../components/EmptyState'
import { ErrorBox } from '../../components/ErrorBox'
import { Pill } from '../../components/Pill'
import { Select } from '../../components/Select'
import { PageSpinner } from '../../components/Spinner'
import { api } from '../../lib/api'
import { useCampusFilter } from '../../lib/auth'
import { cx, pluralize } from '../../lib/format'
import type { AnalyticsQuery, Campus, Page, TrendingCourse } from '../../lib/types'

/**
 * FR 4.4 -- the trending courses dashboard.
 *
 * Demand is the weighted aggregate of `course_demand_events` over a rolling
 * 30 days: request x3, wishlist x2, search x1,
 * view x0.5. The interesting rows are the ones with a tall bar and zero
 * available listings -- that is demand nobody is serving.
 */

const LIMIT = 20

/** Analytics routes return a ranked array; the paged envelope is tolerated too. */
function toItems<T>(data: T[] | Page<T> | undefined): T[] {
  if (!data) return []
  return Array.isArray(data) ? data : data.items
}

/** The four demand signals, in descending weight order. */
type SignalKey = 'request_count' | 'wishlist_count' | 'search_count' | 'view_count'

const SIGNAL_WEIGHTS: { key: SignalKey; label: string; weight: string }[] = [
  { key: 'request_count', label: 'requests', weight: '×3' },
  { key: 'wishlist_count', label: 'wishlisted', weight: '×2' },
  { key: 'search_count', label: 'searches', weight: '×1' },
  { key: 'view_count', label: 'views', weight: '×0.5' },
]

function Signal({ count, label, weight }: { count: number; label: string; weight: string }) {
  return (
    <span className="inline-flex items-baseline gap-1" title={`Weighted ${weight} in the score`}>
      <span className="text-sm font-semibold tabular-nums text-slate-800">{count}</span>
      <span className="text-xs text-slate-500">{label}</span>
    </span>
  )
}

export default function TrendingPage() {
  const { campusId, setCampusId } = useCampusFilter()

  const campusesQuery = useQuery({
    queryKey: ['campuses'],
    queryFn: () => api.get<Campus[]>('/campuses'),
    staleTime: 60 * 60 * 1000,
  })

  const trendingQuery = useQuery({
    queryKey: ['analytics', 'trending', { campus_id: campusId, limit: LIMIT }],
    queryFn: () =>
      api.get<TrendingCourse[] | Page<TrendingCourse>>('/analytics/trending-courses', {
        query: {
          campus_id: campusId ?? undefined,
          limit: LIMIT,
        } satisfies AnalyticsQuery,
      }),
    staleTime: 5 * 60 * 1000,
  })

  const courses = toItems(trendingQuery.data)
  // Bars are relative to the busiest course, so the shape of the demand curve
  // is readable even when the absolute scores are small.
  const topScore = courses.reduce((max, course) => Math.max(max, course.demand_score), 0)
  const campusName = campusesQuery.data?.find((c) => c.campus_id === campusId)?.name

  return (
    <div className="mx-auto max-w-4xl">
      <header className="mb-6 flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-slate-900">Trending courses</h1>
          <p className="mt-1 max-w-2xl text-sm text-slate-500">
            What students are looking for{campusName ? ` at ${campusName}` : ' across every campus'},
            and whether anyone is listing it.
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

      <p className="mb-4 rounded-lg bg-brand-50 px-4 py-3 text-sm leading-relaxed text-brand-900 ring-1 ring-inset ring-brand-100">
        <span className="font-semibold">Demand</span> is a rolling 30-day signal, weighted by how
        much intent each action shows: a request counts ×3, a wishlist save ×2, a search ×1 and a
        page view ×0.5.
      </p>

      {trendingQuery.isError ? (
        <ErrorBox
          error={trendingQuery.error}
          title="Could not load trending courses"
          onRetry={() => void trendingQuery.refetch()}
        />
      ) : trendingQuery.isPending ? (
        <PageSpinner label="Measuring demand" />
      ) : courses.length === 0 ? (
        <EmptyState
          title="No demand recorded yet"
          description={
            campusId
              ? 'Nobody on this campus has searched, viewed, requested or wishlisted anything in the last 30 days. Try all campuses.'
              : 'Searches, views, requests and wishlist saves from the last 30 days feed this dashboard. It fills in as soon as people start browsing.'
          }
          action={
            <Link to="/" className="btn btn-ghost">
              Go and browse the catalogue
            </Link>
          }
        />
      ) : (
        <div className="card overflow-hidden">
          <ul className="divide-y divide-slate-100">
            {courses.map((course) => {
              const width = topScore > 0 ? Math.max(2, (course.demand_score / topScore) * 100) : 0
              const unserved = course.available_listings === 0

              return (
                <li key={course.course_code} className="px-5 py-4">
                  <div className="flex items-start gap-4">
                    <span className="mt-0.5 inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-white text-xs font-semibold tabular-nums text-slate-500 ring-1 ring-inset ring-slate-200">
                      {course.rank}
                    </span>

                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                        <h2 className="text-base font-semibold text-slate-900">
                          {course.course_code}
                        </h2>
                        <span className="text-sm text-slate-500">
                          {course.department ?? 'Department unknown'}
                        </span>
                        <span className="ml-auto text-sm font-medium tabular-nums text-slate-600">
                          {course.demand_score.toFixed(1)}{' '}
                          <span className="text-xs font-normal text-slate-400">demand</span>
                        </span>
                      </div>

                      {/* Width is a computed proportion, so it has to be inline. */}
                      <div
                        className="mt-2 h-2 w-full overflow-hidden rounded-full bg-slate-100"
                        role="img"
                        aria-label={`Demand score ${course.demand_score.toFixed(1)} of ${topScore.toFixed(1)}`}
                      >
                        <div
                          className={cx(
                            'h-full rounded-full',
                            unserved ? 'bg-state-pending' : 'bg-brand-500',
                          )}
                          style={{ width: `${width}%` }}
                        />
                      </div>

                      <div className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-2">
                        {SIGNAL_WEIGHTS.map((signal) => (
                          <Signal
                            key={signal.key}
                            count={course[signal.key]}
                            label={signal.label}
                            weight={signal.weight}
                          />
                        ))}

                        <span className="ml-auto">
                          {unserved ? (
                            <Pill color="bg-state-pending/10 text-state-pending ring-state-pending/20">
                              Nothing listed
                            </Pill>
                          ) : (
                            <Link to={`/?course_code=${encodeURIComponent(course.course_code)}`}>
                              <Pill color="bg-state-approved/10 text-state-approved ring-state-approved/20">
                                {pluralize(course.available_listings, 'listing')} available
                              </Pill>
                            </Link>
                          )}
                        </span>
                      </div>

                      {unserved ? (
                        <p className="mt-3 rounded-lg bg-state-pending/5 px-3 py-2 text-xs leading-relaxed text-slate-600 ring-1 ring-inset ring-state-pending/20">
                          Nobody is listing this yet — students are asking for {course.course_code}{' '}
                          and finding nothing.{' '}
                          <Link
                            to="/upload"
                            className="font-semibold text-brand-700 underline underline-offset-2 hover:text-brand-800"
                          >
                            Upload your copy
                          </Link>
                          .
                        </p>
                      ) : null}
                    </div>
                  </div>
                </li>
              )
            })}
          </ul>
        </div>
      )}
    </div>
  )
}
