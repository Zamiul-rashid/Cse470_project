import { useCallback, useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { Button } from '../../components/Button'
import { EmptyState } from '../../components/EmptyState'
import { ErrorBox } from '../../components/ErrorBox'
import { Input } from '../../components/Input'
import { ListingCard } from '../../components/ListingCard'
import { Pagination } from '../../components/Pagination'
import { Select } from '../../components/Select'
import { useToast } from '../../components/Toast'
import { api } from '../../lib/api'
import { useCampusFilter } from '../../lib/auth'
import { cx, listingTypeColor, listingTypeLabel } from '../../lib/format'
import {
  LISTING_TYPES,
  type Campus,
  type ListingType,
  type Material,
  type MaterialSort,
  type Page,
} from '../../lib/types'

/*
 * FR 1.2 / 1.3 / 4.3 -- browse, search and filter.
 *
 * Every filter lives in the URL query string. That is what makes a result set
 * shareable, makes the back button step through filter changes, and lets a
 * reload land on the same results. There is no duplicate copy of the filter
 * state in React -- `searchParams` *is* the state, and the only exception is
 * the search box, which keeps a local value so typing stays responsive and is
 * flushed to the URL on a debounce.
 */

const PAGE_SIZE = 12
const SEARCH_DEBOUNCE_MS = 350

/**
 * Campus is the one filter with a default that does not come from the URL: it
 * follows the header scope (FR 4.3) until the user picks something here. The
 * `any` sentinel is how "all campuses" is spelled in the URL, because an empty
 * value would be indistinguishable from "not set".
 */
const ALL_CAMPUSES = 'any'

const SORT_OPTIONS: { value: MaterialSort; label: string }[] = [
  { value: 'newest', label: 'Newest first' },
  { value: 'price_asc', label: 'Price: low to high' },
  { value: 'price_desc', label: 'Price: high to low' },
  { value: 'rating', label: 'Best rated uploader' },
]

function isListingType(value: string): value is ListingType {
  return (LISTING_TYPES as readonly string[]).includes(value)
}

function isSort(value: string | null): value is MaterialSort {
  return SORT_OPTIONS.some((option) => option.value === value)
}

/** Shape sent to `GET /materials`. `listing_type` repeats for a multi-select. */
interface BrowseQuery {
  q?: string
  course_code?: string
  department?: string
  semester?: string
  listing_type?: ListingType[]
  campus_id?: string
  min_price?: number
  max_price?: number
  sort: MaterialSort
  page: number
  page_size: number
}

function CardSkeleton() {
  return (
    <div className="card overflow-hidden">
      <div className="h-1 w-full bg-slate-200" />
      <div className="animate-pulse space-y-3 p-4">
        <div className="h-4 w-20 rounded-full bg-slate-200" />
        <div className="h-4 w-3/4 rounded bg-slate-200" />
        <div className="h-3 w-1/2 rounded bg-slate-100" />
        <div className="h-5 w-16 rounded bg-slate-200" />
        <div className="h-3 w-full rounded bg-slate-100" />
      </div>
    </div>
  )
}

export default function BrowsePage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const { campusId: headerCampusId } = useCampusFilter()
  const queryClient = useQueryClient()
  const toast = useToast()

  const [filtersOpen, setFiltersOpen] = useState(false)

  // ------------------------------------------------------------- read state
  const q = searchParams.get('q') ?? ''
  const courseCode = searchParams.get('course_code') ?? ''
  const department = searchParams.get('department') ?? ''
  const semester = searchParams.get('semester') ?? ''
  const minPrice = searchParams.get('min_price') ?? ''
  const maxPrice = searchParams.get('max_price') ?? ''
  const types = searchParams.getAll('listing_type').filter(isListingType)
  const sortParam = searchParams.get('sort')
  const sort: MaterialSort = isSort(sortParam) ? sortParam : 'newest'
  const page = Math.max(1, Number(searchParams.get('page') ?? '1') || 1)

  const campusParam = searchParams.get('campus_id')
  const selectedCampus = campusParam === null ? (headerCampusId ?? ALL_CAMPUSES) : campusParam
  const effectiveCampusId = selectedCampus === ALL_CAMPUSES ? undefined : selectedCampus

  const hasFilters =
    Boolean(q || courseCode || department || semester || minPrice || maxPrice) ||
    types.length > 0 ||
    effectiveCampusId !== undefined

  // ------------------------------------------------------------ write state
  const commit = useCallback(
    (mutate: (params: URLSearchParams) => void, options?: { replace?: boolean; keepPage?: boolean }) => {
      const next = new URLSearchParams(searchParams)
      mutate(next)
      if (!options?.keepPage) next.delete('page')
      setSearchParams(next, { replace: options?.replace ?? false })
    },
    [searchParams, setSearchParams],
  )

  const setFilter = useCallback(
    (key: string, value: string) => {
      commit((params) => {
        if (value) params.set(key, value)
        else params.delete(key)
      })
    },
    [commit],
  )

  const toggleType = useCallback(
    (type: ListingType) => {
      commit((params) => {
        const current = params.getAll('listing_type').filter(isListingType)
        const next = current.includes(type)
          ? current.filter((item) => item !== type)
          : [...current, type]
        params.delete('listing_type')
        next.forEach((item) => params.append('listing_type', item))
      })
    },
    [commit],
  )

  const clearFilters = useCallback(() => {
    setSearchParams(new URLSearchParams(), { replace: false })
  }, [setSearchParams])

  // --------------------------------------------------- debounced search box
  const [searchInput, setSearchInput] = useState(q)

  // Keeps the box honest when the URL changes underneath it (back button,
  // "clear filters", a shared link).
  useEffect(() => {
    setSearchInput(q)
  }, [q])

  useEffect(() => {
    if (searchInput === q) return
    const timer = window.setTimeout(() => {
      // `replace` so a half-typed query does not fill the history stack.
      commit((params) => {
        if (searchInput.trim()) params.set('q', searchInput.trim())
        else params.delete('q')
      }, { replace: true })
    }, SEARCH_DEBOUNCE_MS)
    return () => window.clearTimeout(timer)
  }, [searchInput, q, commit])

  // ----------------------------------------------------------------- data
  const query: BrowseQuery = {
    q: q || undefined,
    course_code: courseCode || undefined,
    department: department || undefined,
    semester: semester || undefined,
    listing_type: types.length > 0 ? types : undefined,
    campus_id: effectiveCampusId,
    min_price: minPrice ? Number(minPrice) : undefined,
    max_price: maxPrice ? Number(maxPrice) : undefined,
    sort,
    page,
    page_size: PAGE_SIZE,
  }

  const campusesQuery = useQuery({
    queryKey: ['campuses'],
    queryFn: () => api.get<Campus[]>('/campuses'),
    staleTime: 60 * 60 * 1000,
  })

  const listingsQuery = useQuery({
    queryKey: ['materials', 'browse', query],
    queryFn: () => api.get<Page<Material>>('/materials', { query }),
    placeholderData: keepPreviousData,
  })

  // FR 2.4 -- bookmark straight from the grid.
  const wishlist = useMutation({
    mutationFn: (listing: Material) =>
      listing.is_bookmarked
        ? api.del<void>(`/wishlist/items/${listing.listing_id}`)
        : api.post<void>('/wishlist/items', { listing_id: listing.listing_id }),
    onSuccess: async (_data, listing) => {
      await queryClient.invalidateQueries({ queryKey: ['materials'] })
      await queryClient.invalidateQueries({ queryKey: ['wishlist'] })
      toast.success(listing.is_bookmarked ? 'Removed from your wishlist' : 'Saved to your wishlist')
    },
    onError: (cause) => toast.fromError(cause, 'Could not update your wishlist'),
  })

  const results = listingsQuery.data
  const items = results?.items ?? []
  const campuses = campusesQuery.data ?? []

  // ---------------------------------------------------------------- render
  const sidebar = (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-slate-900">Filters</h2>
        {hasFilters ? (
          <button
            type="button"
            onClick={clearFilters}
            className="text-xs font-medium text-brand-700 hover:text-brand-800"
          >
            Clear all
          </button>
        ) : null}
      </div>

      {/* FR 1.3 -- listing type is a multi-select, rendered as chips. */}
      <fieldset>
        <legend className="label">Listing type</legend>
        <div className="flex flex-wrap gap-1.5">
          {LISTING_TYPES.map((type) => {
            const active = types.includes(type)
            return (
              <button
                key={type}
                type="button"
                aria-pressed={active}
                onClick={() => toggleType(type)}
                className={cx(
                  'pill transition-colors',
                  active
                    ? listingTypeColor(type)
                    : 'bg-white text-slate-600 ring-slate-300 hover:bg-slate-50',
                )}
              >
                {listingTypeLabel(type)}
              </button>
            )
          })}
        </div>
      </fieldset>

      <Input
        label="Course code"
        placeholder="CSE470"
        value={courseCode}
        onChange={(event) => setFilter('course_code', event.target.value)}
      />

      <Input
        label="Department"
        placeholder="Computer Science"
        value={department}
        onChange={(event) => setFilter('department', event.target.value)}
      />

      <Input
        label="Semester"
        placeholder="Spring 2026"
        value={semester}
        onChange={(event) => setFilter('semester', event.target.value)}
      />

      {/* FR 4.3 -- campus scope, defaulting to the header selection. */}
      <Select
        label="Campus"
        value={selectedCampus}
        disabled={campusesQuery.isPending}
        onChange={(event) => setFilter('campus_id', event.target.value)}
      >
        <option value={ALL_CAMPUSES}>All campuses</option>
        {campuses.map((campus) => (
          <option key={campus.campus_id} value={campus.campus_id}>
            {campus.name}
          </option>
        ))}
      </Select>

      <fieldset>
        <legend className="label">Price range</legend>
        <div className="flex items-center gap-2">
          <Input
            aria-label="Minimum price"
            type="number"
            min={0}
            inputMode="numeric"
            placeholder="Min"
            prefix="৳"
            value={minPrice}
            onChange={(event) => setFilter('min_price', event.target.value)}
          />
          <span className="text-sm text-slate-400">–</span>
          <Input
            aria-label="Maximum price"
            type="number"
            min={0}
            inputMode="numeric"
            placeholder="Max"
            prefix="৳"
            value={maxPrice}
            onChange={(event) => setFilter('max_price', event.target.value)}
          />
        </div>
        <p className="mt-1.5 text-xs text-slate-500">
          Free and exchange listings carry no price and are unaffected by this.
        </p>
      </fieldset>
    </div>
  )

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900">Browse material</h1>
          <p className="mt-1 text-sm text-slate-500">
            Notes, textbooks and past papers shared by students on your campus.
          </p>
        </div>
        <Link to="/upload" className="btn btn-primary">
          Upload material
        </Link>
      </header>

      {/* Search runs across title, course code, department and description. */}
      <div className="flex flex-wrap items-center gap-3">
        <Input
          containerClassName="min-w-0 flex-1"
          aria-label="Search materials"
          type="search"
          placeholder="Search by title, course code or keyword…"
          value={searchInput}
          prefix={
            <svg
              aria-hidden="true"
              viewBox="0 0 20 20"
              fill="none"
              stroke="currentColor"
              strokeWidth={1.8}
              className="h-4 w-4"
            >
              <path strokeLinecap="round" d="M13 13l4 4m-1.5-8.5a6.5 6.5 0 1 1-13 0 6.5 6.5 0 0 1 13 0Z" />
            </svg>
          }
          onChange={(event) => setSearchInput(event.target.value)}
        />

        <Select
          containerClassName="w-full sm:w-56"
          aria-label="Sort results"
          value={sort}
          options={SORT_OPTIONS}
          onChange={(event) => setFilter('sort', event.target.value)}
        />

        <Button
          variant="ghost"
          className="lg:hidden"
          onClick={() => setFiltersOpen((open) => !open)}
          aria-expanded={filtersOpen}
        >
          {filtersOpen ? 'Hide filters' : 'Filters'}
          {types.length > 0 ? (
            <span className="rounded-full bg-brand-600 px-1.5 text-xs font-semibold text-white">
              {types.length}
            </span>
          ) : null}
        </Button>
      </div>

      <div className="grid gap-6 lg:grid-cols-[16rem,1fr]">
        {/* Sidebar: always present on large screens, toggled below. */}
        <aside className={cx('lg:block', filtersOpen ? 'block' : 'hidden')}>
          <div className="card p-5 lg:sticky lg:top-24">{sidebar}</div>
        </aside>

        <section>
          <div className="mb-3 flex items-center justify-between gap-3">
            <p className="text-sm text-slate-500" aria-live="polite">
              {listingsQuery.isPending
                ? 'Searching…'
                : results
                  ? `${results.total} ${results.total === 1 ? 'result' : 'results'}`
                  : ''}
            </p>
            {listingsQuery.isFetching && !listingsQuery.isPending ? (
              <p className="text-xs text-slate-400">Updating…</p>
            ) : null}
          </div>

          {listingsQuery.isError ? (
            <ErrorBox
              error={listingsQuery.error}
              title="Could not load listings"
              onRetry={() => void listingsQuery.refetch()}
            />
          ) : listingsQuery.isPending ? (
            <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
              {Array.from({ length: 6 }, (_, index) => (
                <CardSkeleton key={index} />
              ))}
            </div>
          ) : items.length === 0 ? (
            <EmptyState
              title={hasFilters ? 'Nothing matches these filters' : 'No material listed yet'}
              description={
                hasFilters
                  ? 'Try a broader course code, drop the price range, or widen the campus scope.'
                  : 'Be the first to share something. Uploads appear here once a moderator approves them.'
              }
              action={
                hasFilters ? (
                  <Button variant="ghost" onClick={clearFilters}>
                    Clear all filters
                  </Button>
                ) : (
                  <Link to="/upload" className="btn btn-primary">
                    Upload material
                  </Link>
                )
              }
            />
          ) : (
            <>
              <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
                {items.map((listing) => (
                  <ListingCard
                    key={listing.listing_id}
                    listing={listing}
                    onToggleWishlist={(item) => wishlist.mutate(item)}
                    wishlistPending={
                      wishlist.isPending && wishlist.variables?.listing_id === listing.listing_id
                    }
                  />
                ))}
              </div>

              <Pagination
                className="mt-6"
                page={results?.page ?? page}
                pages={results?.pages ?? 1}
                total={results?.total}
                pageSize={results?.page_size ?? PAGE_SIZE}
                onPageChange={(nextPage) => {
                  commit((params) => params.set('page', String(nextPage)), { keepPage: true })
                  window.scrollTo({ top: 0, behavior: 'smooth' })
                }}
              />
            </>
          )}
        </section>
      </div>
    </div>
  )
}
