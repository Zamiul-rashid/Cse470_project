import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { Button } from '../../components/Button'
import { EmptyState } from '../../components/EmptyState'
import { ErrorBox } from '../../components/ErrorBox'
import { Input } from '../../components/Input'
import { Modal } from '../../components/Modal'
import { Pagination } from '../../components/Pagination'
import { ListingTypePill, Pill } from '../../components/Pill'
import { Select } from '../../components/Select'
import { PageSpinner } from '../../components/Spinner'
import { StatusPill } from '../../components/StatusPill'
import { TextArea } from '../../components/TextArea'
import { useToast } from '../../components/Toast'
import { api } from '../../lib/api'
import { useAuth, useCampusFilter } from '../../lib/auth'
import { currency, cx, initials, listingTypeLabel, relativeTime } from '../../lib/format'
import { LISTING_TYPES } from '../../lib/types'
import type {
  Campus,
  ListingType,
  Page,
  RequestCreate,
  RequestRead,
  RequestUpdate,
} from '../../lib/types'

/**
 * FR 2.1 -- the request board.
 *
 * A Request is the *forward-looking* saved search:
 * the matcher runs its criteria against every newly approved listing, which is
 * why the form collects structured fields rather than a sentence of free text.
 */

const PAGE_SIZE = 12

const requestKeys = {
  all: ['requests'] as const,
  board: (params: object) => ['requests', 'board', params] as const,
  mine: () => ['requests', 'mine'] as const,
}

interface RequestFormState {
  course_code: string
  department: string
  edition: string
  listing_type: '' | ListingType
  max_price: string
  campus_id: string
  description: string
}

const EMPTY_FORM: RequestFormState = {
  course_code: '',
  department: '',
  edition: '',
  listing_type: '',
  max_price: '',
  campus_id: '',
  description: '',
}

type FormErrors = Partial<Record<keyof RequestFormState, string>>

/** Empty string -> null, so the server stores "any" rather than "". */
function orNull(value: string): string | null {
  const trimmed = value.trim()
  return trimmed === '' ? null : trimmed
}

// ---------------------------------------------------------------------------
// one row on the board
// ---------------------------------------------------------------------------

interface RequestCardProps {
  request: RequestRead
  /** Present only on the caller's own requests. */
  onClose?: (request: RequestRead) => void
  closing?: boolean
}

function RequestCard({ request, onClose, closing = false }: RequestCardProps) {
  const criteria = [
    request.department ? { key: 'dept', label: request.department } : null,
    request.edition ? { key: 'edition', label: `Edition ${request.edition}` } : null,
    request.max_price !== null && request.max_price !== undefined
      ? { key: 'price', label: `Up to ${currency(request.max_price)}` }
      : null,
    request.campus_name
      ? { key: 'campus', label: request.campus_name }
      : { key: 'campus', label: 'Any campus' },
  ].filter((chip): chip is { key: string; label: string } => chip !== null)

  return (
    <li className="card p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-3">
          <span
            aria-hidden="true"
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-brand-100 text-xs font-semibold text-brand-800"
          >
            {initials(request.requester_name)}
          </span>
          <div className="min-w-0">
            <p className="text-sm font-semibold text-slate-900">
              {request.course_code}
              <span className="ml-2 font-normal text-slate-500">
                {request.requester_name ?? 'A student'}
              </span>
            </p>
            <p className="mt-0.5 text-xs text-slate-400">
              Asked {relativeTime(request.created_at)}
            </p>
          </div>
        </div>

        <div className="flex shrink-0 items-center gap-2">
          <StatusPill status={request.status} kind="request" />
          {onClose && request.status !== 'CLOSED' ? (
            <Button variant="ghost" size="sm" loading={closing} onClick={() => onClose(request)}>
              Close
            </Button>
          ) : null}
        </div>
      </div>

      {request.description ? (
        <p className="mt-3 text-sm leading-relaxed text-slate-600">{request.description}</p>
      ) : null}

      <div className="mt-3 flex flex-wrap items-center gap-1.5">
        {request.listing_type ? (
          <ListingTypePill type={request.listing_type} />
        ) : (
          <Pill>Any listing type</Pill>
        )}
        {criteria.map((chip) => (
          <Pill key={chip.key}>{chip.label}</Pill>
        ))}
      </div>
    </li>
  )
}

// ---------------------------------------------------------------------------
// page
// ---------------------------------------------------------------------------

export default function RequestsPage() {
  const { user } = useAuth()
  const { campusId } = useCampusFilter()
  const queryClient = useQueryClient()
  const toast = useToast()

  const [courseInput, setCourseInput] = useState('')
  const [courseFilter, setCourseFilter] = useState('')
  const [page, setPage] = useState(1)
  const [modalOpen, setModalOpen] = useState(false)
  const [form, setForm] = useState<RequestFormState>(EMPTY_FORM)
  const [errors, setErrors] = useState<FormErrors>({})
  const [closingId, setClosingId] = useState<string | null>(null)

  // Typing a course code should not fire a request per keystroke.
  useEffect(() => {
    const timer = window.setTimeout(() => {
      setCourseFilter(courseInput.trim())
      setPage(1)
    }, 300)
    return () => window.clearTimeout(timer)
  }, [courseInput])

  const boardParams = useMemo(
    () => ({
      course_code: courseFilter || undefined,
      campus_id: campusId ?? undefined,
      status: 'OPEN' as const,
      page,
      page_size: PAGE_SIZE,
    }),
    [courseFilter, campusId, page],
  )

  const boardQuery = useQuery({
    queryKey: requestKeys.board(boardParams),
    queryFn: () => api.get<Page<RequestRead>>('/requests', { query: boardParams }),
    placeholderData: keepPreviousData,
  })

  const mineQuery = useQuery({
    queryKey: requestKeys.mine(),
    queryFn: () =>
      api.get<Page<RequestRead>>('/requests', { query: { mine: true, page: 1, page_size: 50 } }),
  })

  const campusesQuery = useQuery({
    queryKey: ['campuses'],
    queryFn: () => api.get<Campus[]>('/campuses'),
    staleTime: 60 * 60 * 1000,
  })

  const createRequest = useMutation({
    mutationFn: (payload: RequestCreate) => api.post<RequestRead>('/requests', payload),
    onSuccess: (created) => {
      void queryClient.invalidateQueries({ queryKey: requestKeys.all })
      setModalOpen(false)
      setForm(EMPTY_FORM)
      setErrors({})
      toast.success(
        'Request posted',
        `We will notify you when a ${created.course_code} listing matches it.`,
      )
    },
    onError: (error: unknown) => toast.fromError(error, 'Could not post the request'),
  })

  const closeRequest = useMutation({
    mutationFn: (requestId: string) => {
      const payload: RequestUpdate = { status: 'CLOSED' }
      return api.patch<RequestRead>(`/requests/${requestId}`, payload)
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: requestKeys.all })
      toast.success('Request closed', 'You will stop getting matches for it.')
    },
    onError: (error: unknown) => toast.fromError(error, 'Could not close the request'),
    onSettled: () => setClosingId(null),
  })

  const myRequests = mineQuery.data?.items ?? []
  const openMine = myRequests.filter((request) => request.status !== 'CLOSED')
  // Own requests already have their own section above the board.
  const board = (boardQuery.data?.items ?? []).filter(
    (request) => request.requester_id !== user?.user_id,
  )

  /** Validation only runs on submit, so any edit clears the previous messages. */
  const patch = (next: Partial<RequestFormState>): void => {
    setForm((current) => ({ ...current, ...next }))
    setErrors({})
  }

  const handleSubmit = (event: FormEvent<HTMLFormElement>): void => {
    event.preventDefault()

    const nextErrors: FormErrors = {}
    if (!form.course_code.trim()) nextErrors.course_code = 'A course code is required.'
    if (form.max_price.trim()) {
      const parsed = Number(form.max_price)
      if (!Number.isFinite(parsed) || parsed < 0) {
        nextErrors.max_price = 'Enter a number, or leave it blank.'
      }
    }
    if (Object.keys(nextErrors).length > 0) {
      setErrors(nextErrors)
      return
    }

    const payload: RequestCreate = {
      course_code: form.course_code.trim(),
      department: orNull(form.department),
      edition: orNull(form.edition),
      listing_type: form.listing_type === '' ? null : form.listing_type,
      max_price: form.max_price.trim() ? Number(form.max_price) : null,
      campus_id: orNull(form.campus_id),
      description: orNull(form.description),
    }
    createRequest.mutate(payload)
  }

  const openModal = (): void => {
    setForm({ ...EMPTY_FORM, campus_id: campusId ?? '' })
    setErrors({})
    setModalOpen(true)
  }

  return (
    <div className="space-y-8">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="max-w-2xl">
          <h1 className="text-xl font-semibold tracking-tight text-slate-900">Request board</h1>
          <p className="mt-1.5 text-sm leading-relaxed text-slate-500">
            Cannot find a book or a note set? Post what you are after. A request doubles as a saved
            search: whenever an approved listing matches it, we notify you straight away.
          </p>
        </div>
        <Button onClick={openModal}>Post a request</Button>
      </header>

      {/* ---- own requests ------------------------------------------------ */}
      <section className="space-y-3">
        <div className="flex items-baseline justify-between gap-3">
          <h2 className="text-sm font-semibold text-slate-900">Your requests</h2>
          <p className="text-xs text-slate-400">
            {openMine.length > 0
              ? `${openMine.length} watching for matches`
              : 'Nothing being watched'}
          </p>
        </div>

        {mineQuery.isError ? (
          <ErrorBox error={mineQuery.error} onRetry={() => void mineQuery.refetch()} />
        ) : myRequests.length === 0 ? (
          <p className="card px-4 py-6 text-center text-sm text-slate-500">
            You have not posted a request yet. Posting one puts you in front of every student who
            uploads for that course.
          </p>
        ) : (
          <ul className="space-y-3">
            {myRequests.map((request) => (
              <RequestCard
                key={request.request_id}
                request={request}
                closing={closingId === request.request_id}
                onClose={(target) => {
                  setClosingId(target.request_id)
                  closeRequest.mutate(target.request_id)
                }}
              />
            ))}
          </ul>
        )}
      </section>

      {/* ---- the board --------------------------------------------------- */}
      <section className="space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-sm font-semibold text-slate-900">Open requests from other students</h2>
          <Input
            value={courseInput}
            onChange={(event) => setCourseInput(event.target.value)}
            placeholder="Filter by course code, e.g. CSE470"
            aria-label="Filter by course code"
            containerClassName="w-full sm:w-72"
          />
        </div>

        {boardQuery.isPending ? (
          <PageSpinner label="Loading the request board" />
        ) : boardQuery.isError ? (
          <ErrorBox error={boardQuery.error} onRetry={() => void boardQuery.refetch()} />
        ) : board.length === 0 ? (
          <EmptyState
            title="No open requests here"
            description={
              courseFilter
                ? `Nobody is currently looking for ${courseFilter} on this campus. Try clearing the filter.`
                : 'When students ask for material you already have, their requests show up here.'
            }
            action={<Button onClick={openModal}>Post a request</Button>}
          />
        ) : (
          <>
            <ul className={cx('space-y-3', boardQuery.isFetching && 'opacity-60 transition-opacity')}>
              {board.map((request) => (
                <RequestCard key={request.request_id} request={request} />
              ))}
            </ul>
            <Pagination
              page={boardQuery.data?.page ?? page}
              pages={boardQuery.data?.pages ?? 1}
              total={boardQuery.data?.total}
              pageSize={boardQuery.data?.page_size}
              onPageChange={setPage}
            />
          </>
        )}
      </section>

      {/* ---- post a request ---------------------------------------------- */}
      <Modal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        title="Post a request"
        description="Only the course code is required. Every other field narrows what counts as a match."
        size="lg"
        dismissable={!createRequest.isPending}
        footer={
          <>
            <Button
              variant="ghost"
              onClick={() => setModalOpen(false)}
              disabled={createRequest.isPending}
            >
              Cancel
            </Button>
            <Button type="submit" form="post-request-form" loading={createRequest.isPending}>
              Post request
            </Button>
          </>
        }
      >
        <form id="post-request-form" onSubmit={handleSubmit} className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <Input
              label="Course code"
              required
              value={form.course_code}
              error={errors.course_code}
              onChange={(event) => patch({ course_code: event.target.value })}
              placeholder="CSE470"
            />
            <Input
              label="Department"
              value={form.department}
              onChange={(event) => patch({ department: event.target.value })}
              placeholder="Computer Science"
              hint="Leave blank to match any department."
            />
            <Input
              label="Edition"
              value={form.edition}
              onChange={(event) => patch({ edition: event.target.value })}
              placeholder="3rd"
            />
            <Select
              label="Listing type"
              placeholder="Any type"
              value={form.listing_type}
              onChange={(event) => patch({ listing_type: event.target.value as '' | ListingType })}
              options={LISTING_TYPES.map((type) => ({
                value: type,
                label: listingTypeLabel(type),
              }))}
            />
            <Input
              label="Maximum price"
              type="number"
              min={0}
              step="1"
              prefix="৳"
              value={form.max_price}
              error={errors.max_price}
              onChange={(event) => patch({ max_price: event.target.value })}
              placeholder="500"
              hint="Matches above this price are skipped."
            />
            <Select
              label="Campus"
              placeholder="Any campus"
              value={form.campus_id}
              onChange={(event) => patch({ campus_id: event.target.value })}
              options={(campusesQuery.data ?? []).map((campus) => ({
                value: campus.campus_id,
                label: campus.name,
              }))}
            />
          </div>

          <TextArea
            label="Anything else?"
            value={form.description}
            maxLength={500}
            showCount
            onChange={(event) => patch({ description: event.target.value })}
            placeholder="Solved problem sets would be ideal, but the textbook alone is fine."
          />
        </form>
      </Modal>
    </div>
  )
}
