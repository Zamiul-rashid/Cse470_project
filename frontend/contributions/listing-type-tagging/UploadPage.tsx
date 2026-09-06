import { useEffect, useRef, useState, type ChangeEvent, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { Button } from '../../components/Button'
import { ErrorBox } from '../../components/ErrorBox'
import { Input } from '../../components/Input'
import { Spinner } from '../../components/Spinner'
import { TextArea } from '../../components/TextArea'
import { useToast } from '../../components/Toast'
import { api } from '../../lib/api'
import {
  currency,
  cx,
  formatBytes,
  listingTypeColor,
  listingTypeLabel,
  pluralize,
} from '../../lib/format'
import {
  LISTING_TYPES,
  isPricedListingType,
  type DuplicateCheckRequest,
  type DuplicateCheckResponse,
  type ListingType,
  type Material,
  type PriceBasis,
  type PriceSuggestion,
} from '../../lib/types'

/*
 * FR 1.1 (upload), 1.3 (listing type), 1.5 (duplicate warning) and 3.1 (price
 * suggestion) all land on this one form.
 *
 * Two of those are live rather than on submit: as the title, course code and
 * edition settle the form asks the server whether this looks like a duplicate,
 * and once a course code and a priced listing type are present it asks what
 * comparable listings went for. Neither one blocks the upload. The duplicate
 * check *warns* -- two students selling the same
 * textbook is the normal case -- and the price suggestion is advisory, with an
 * honest "not enough data" instead of a fabricated number.
 */

/** Mirrors the server-side cap, checked here so a 25 MB round trip is avoided. */
const MAX_FILE_BYTES = 25 * 1024 * 1024
const ACCEPTED_MIME: readonly string[] = ['application/pdf', 'image/png', 'image/jpeg']
const ACCEPTED_EXTENSIONS = /\.(pdf|png|jpe?g)$/i
const DUPLICATE_DEBOUNCE_MS = 500

/**
 * What the suggestion is actually based on. Every rung of the ladder in
 * `services/recommendation.py` gets its own sentence, because "৳450 based on
 * the department average" is a materially weaker claim than "৳450 for this
 * exact course" and the panel must not let the two read alike.
 */
const BASIS_NOTE: Record<PriceBasis, string> = {
  course_edition: 'Based on listings for this exact course and edition.',
  course: 'Based on listings for this course, across every edition.',
  department:
    'There were not enough listings for this exact course, so the sample was widened to the same department.',
  campus:
    'There were not enough listings for this course or department, so the sample was widened to every listing on your campus.',
  insufficient_data: 'Not enough comparable listings to put a number on.',
}

type FieldName =
  | 'file'
  | 'title'
  | 'course_code'
  | 'department'
  | 'semester'
  | 'price'
  | 'acknowledge'

type FieldErrors = Partial<Record<FieldName, string>>

/** Value that only settles once the user has stopped typing. */
function useDebounced<T>(value: T, delay: number): T {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), delay)
    return () => window.clearTimeout(timer)
  }, [value, delay])
  return debounced
}

/**
 * `DuplicateCandidate.similarity` is a 0.0-1.0 ratio (schemas/material.py), so
 * it is scaled exactly once. "0.92% match" reads as a near-certain *non*
 * duplicate, which is the opposite of what the number means.
 */
function similarityPercent(similarity: number): number {
  return Math.round(similarity * 100)
}

/** A file with no MIME type still has a name; do not reject what the server would take. */
function isAcceptedFile(file: File): boolean {
  return file.type ? ACCEPTED_MIME.includes(file.type) : ACCEPTED_EXTENSIONS.test(file.name)
}

export default function UploadPage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const toast = useToast()
  const fileInputRef = useRef<HTMLInputElement>(null)

  const [file, setFile] = useState<File | null>(null)
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [courseCode, setCourseCode] = useState('')
  const [department, setDepartment] = useState('')
  const [semester, setSemester] = useState('')
  const [edition, setEdition] = useState('')
  const [listingType, setListingType] = useState<ListingType>('SELL')
  const [price, setPrice] = useState('')

  const [acknowledged, setAcknowledged] = useState(false)
  const [errors, setErrors] = useState<FieldErrors>({})

  const priced = isPricedListingType(listingType)

  // EXCHANGE and FREE must not carry a price: the study_materials CHECK
  // constraint rejects one outright, so the field is cleared as well as hidden
  // rather than left holding a value the submit would smuggle through.
  useEffect(() => {
    if (!priced) {
      setPrice('')
      setErrors((current) => ({ ...current, price: undefined }))
    }
  }, [priced])

  // ------------------------------------------------- FR 1.5 duplicate check
  const debouncedTitle = useDebounced(title.trim(), DUPLICATE_DEBOUNCE_MS)
  const debouncedCourse = useDebounced(courseCode.trim(), DUPLICATE_DEBOUNCE_MS)
  const debouncedEdition = useDebounced(edition.trim(), DUPLICATE_DEBOUNCE_MS)

  const duplicateEnabled = debouncedTitle.length >= 3 && debouncedCourse.length >= 2

  const duplicateQuery = useQuery({
    queryKey: ['materials', 'check-duplicate', debouncedTitle, debouncedCourse, debouncedEdition],
    queryFn: () => {
      const payload: DuplicateCheckRequest = {
        title: debouncedTitle,
        course_code: debouncedCourse,
        edition: debouncedEdition || null,
      }
      return api.post<DuplicateCheckResponse>('/materials/check-duplicate', payload)
    },
    enabled: duplicateEnabled,
    staleTime: 60 * 1000,
  })

  const candidates = duplicateEnabled ? (duplicateQuery.data?.candidates ?? []) : []
  const needsAcknowledgement = candidates.length > 0

  // A fresh set of warnings has to be acknowledged again -- an "I checked" from
  // before the course code changed says nothing about these listings.
  useEffect(() => {
    setAcknowledged(false)
  }, [duplicateQuery.data])

  // ------------------------------------------------ FR 3.1 price suggestion
  const suggestionEnabled = priced && debouncedCourse.length >= 2

  const suggestionQuery = useQuery({
    queryKey: ['materials', 'price-suggestion', debouncedCourse, debouncedEdition, listingType],
    queryFn: () =>
      api.get<PriceSuggestion>('/materials/price-suggestion', {
        query: {
          course_code: debouncedCourse,
          edition: debouncedEdition || undefined,
          listing_type: listingType,
        },
      }),
    enabled: suggestionEnabled,
    staleTime: 5 * 60 * 1000,
  })

  const suggestion = suggestionEnabled ? suggestionQuery.data : undefined
  const hasNumber =
    suggestion !== undefined &&
    suggestion.basis !== 'insufficient_data' &&
    typeof suggestion.suggested === 'number'

  // ------------------------------------------------------------------ file
  const handleFileChange = (event: ChangeEvent<HTMLInputElement>): void => {
    const picked = event.target.files?.[0] ?? null
    if (!picked) {
      setFile(null)
      return
    }

    const reject = (message: string): void => {
      setErrors((current) => ({ ...current, file: message }))
      setFile(null)
      event.target.value = ''
    }

    if (!isAcceptedFile(picked)) {
      reject('That file type is not supported. Upload a PDF, PNG or JPG.')
      return
    }
    if (picked.size > MAX_FILE_BYTES) {
      reject(
        `That file is ${formatBytes(picked.size)}, and the limit is ${formatBytes(MAX_FILE_BYTES)}. Split it or export it at a lower quality.`,
      )
      return
    }

    setErrors((current) => ({ ...current, file: undefined }))
    setFile(picked)
    // A sensible default title beats an empty one, and it stays editable.
    if (!title.trim()) setTitle(picked.name.replace(/\.[^.]+$/, '').replace(/[_-]+/g, ' ').trim())
  }

  const clearFile = (): void => {
    setFile(null)
    if (fileInputRef.current) fileInputRef.current.value = ''
  }

  // ---------------------------------------------------------------- submit
  const upload = useMutation({
    mutationFn: (form: FormData) => api.upload<Material>('/materials', form),
    onSuccess: async (listing) => {
      await queryClient.invalidateQueries({ queryKey: ['materials'] })
      toast.success(
        'Material uploaded',
        listing.status === 'PENDING'
          ? 'It is awaiting moderation. Nobody can find it in browse until a moderator approves it.'
          : 'It is listed and open to requests.',
      )
      navigate(`/materials/${listing.listing_id}`)
    },
  })

  const validate = (): FieldErrors => {
    const next: FieldErrors = {}
    if (!file) next.file = 'Pick the file you want to list.'
    if (title.trim().length < 3) next.title = 'Give it a title students will recognise.'
    if (courseCode.trim().length < 2) next.course_code = 'Which course is this for?'
    if (!department.trim()) next.department = 'Department is required.'
    if (!semester.trim()) next.semester = 'Semester is required.'
    if (priced) {
      const value = Number(price)
      if (price.trim() === '' || !Number.isFinite(value) || value < 0) {
        next.price = 'Enter a price of zero or more.'
      }
    }
    if (needsAcknowledgement && !acknowledged) {
      next.acknowledge = 'Confirm you have checked the similar listings above.'
    }
    return next
  }

  const handleSubmit = (event: FormEvent<HTMLFormElement>): void => {
    event.preventDefault()

    const nextErrors = validate()
    setErrors(nextErrors)
    if (Object.keys(nextErrors).length > 0 || !file) return

    const form = new FormData()
    form.append('file', file)
    form.append('title', title.trim())
    if (description.trim()) form.append('description', description.trim())
    form.append('course_code', courseCode.trim())
    form.append('department', department.trim())
    form.append('semester', semester.trim())
    if (edition.trim()) form.append('edition', edition.trim())
    form.append('listing_type', listingType)
    // Only SELL and RENT may send this key at all; a price on EXCHANGE or FREE
    // comes back as a 409 from the CHECK constraint.
    if (priced) form.append('price', String(Number(price)))

    upload.mutate(form)
  }

  const submitBlocked = needsAcknowledgement && !acknowledged

  // ---------------------------------------------------------------- render
  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight text-slate-900">Upload material</h1>
        <p className="mt-1 text-sm leading-relaxed text-slate-500">
          Everything you upload goes to a moderator before it appears in browse. Other students only
          ever see a three-page preview until a transaction completes.
        </p>
      </header>

      {upload.isError ? <ErrorBox error={upload.error} title="Could not upload this" /> : null}

      <form className="space-y-6" onSubmit={handleSubmit} noValidate>
        {/* ---------------------------------------------------------- file */}
        <section className="card p-6">
          <h2 className="text-base font-semibold text-slate-900">File</h2>
          <p className="mt-1 text-sm text-slate-500">
            PDF, PNG or JPG, up to {formatBytes(MAX_FILE_BYTES)}.
          </p>

          <div
            className={cx(
              'mt-4 rounded-xl border-2 border-dashed p-6 text-center transition-colors',
              errors.file ? 'border-red-300 bg-red-50' : 'border-slate-300 bg-slate-50',
            )}
          >
            <input
              ref={fileInputRef}
              id="material-file"
              type="file"
              accept={ACCEPTED_MIME.join(',')}
              className="sr-only"
              aria-describedby={errors.file ? 'material-file-error' : undefined}
              onChange={handleFileChange}
            />

            {file ? (
              <div className="flex flex-wrap items-center justify-center gap-3">
                <div className="min-w-0 text-left">
                  <p className="truncate text-sm font-medium text-slate-900">{file.name}</p>
                  <p className="text-xs text-slate-500">
                    {formatBytes(file.size)} · {file.type || 'unknown type'}
                  </p>
                </div>
                <Button variant="ghost" size="sm" onClick={clearFile}>
                  Remove
                </Button>
              </div>
            ) : (
              <>
                <p className="text-sm text-slate-600">No file chosen yet.</p>
                <label htmlFor="material-file" className="btn btn-ghost mt-3 cursor-pointer">
                  Choose a file
                </label>
              </>
            )}
          </div>

          {errors.file ? (
            <p id="material-file-error" className="field-error">
              {errors.file}
            </p>
          ) : null}
        </section>

        {/* ------------------------------------------------------ metadata */}
        <section className="card space-y-4 p-6">
          <h2 className="text-base font-semibold text-slate-900">Details</h2>

          <Input
            label="Title"
            required
            value={title}
            error={errors.title}
            placeholder="Operating Systems — full course notes"
            onChange={(event) => setTitle(event.target.value)}
          />

          <TextArea
            label="Description"
            rows={4}
            maxLength={2000}
            showCount
            value={description}
            placeholder="What does it cover? What condition is it in? Anything a buyer should know."
            onChange={(event) => setDescription(event.target.value)}
          />

          <div className="grid gap-4 sm:grid-cols-2">
            <Input
              label="Course code"
              required
              value={courseCode}
              error={errors.course_code}
              placeholder="CSE470"
              hint="Normalised on the server, so CSE 470 and cse-470 are the same course."
              onChange={(event) => setCourseCode(event.target.value)}
            />
            <Input
              label="Department"
              required
              value={department}
              error={errors.department}
              placeholder="Computer Science"
              onChange={(event) => setDepartment(event.target.value)}
            />
            <Input
              label="Semester"
              required
              value={semester}
              error={errors.semester}
              placeholder="Spring 2026"
              onChange={(event) => setSemester(event.target.value)}
            />
            <Input
              label="Edition"
              value={edition}
              placeholder="3rd edition"
              hint="Optional, but it sharpens both the duplicate check and the price suggestion."
              onChange={(event) => setEdition(event.target.value)}
            />
          </div>
        </section>

        {/* ----------------------------------------- FR 1.5 duplicate panel */}
        {duplicateEnabled && (duplicateQuery.isFetching || candidates.length > 0) ? (
          <section
            aria-live="polite"
            className={cx(
              'rounded-xl p-5 ring-1 ring-inset',
              candidates.length > 0
                ? 'bg-state-pending/5 ring-state-pending/30'
                : 'bg-white ring-slate-200',
            )}
          >
            {candidates.length === 0 ? (
              <p className="flex items-center gap-2 text-sm text-slate-500">
                <Spinner size="sm" label={null} />
                Checking for listings like this one…
              </p>
            ) : (
              <>
                <h2 className="text-sm font-semibold text-state-pending">
                  {candidates.length === 1
                    ? 'A similar listing already exists'
                    : `${candidates.length} similar listings already exist`}
                </h2>
                <p className="mt-1 text-sm leading-relaxed text-slate-600">
                  This is a warning, not a block. Two students listing the same textbook is normal —
                  just make sure you are not re-posting something you already uploaded.
                </p>

                <ul className="mt-4 space-y-2">
                  {candidates.map((candidate) => (
                    <li
                      key={candidate.listing_id}
                      className="rounded-lg bg-white p-3 ring-1 ring-inset ring-slate-200"
                    >
                      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                        <Link
                          to={`/materials/${candidate.listing_id}`}
                          target="_blank"
                          rel="noreferrer"
                          className="text-sm font-medium text-slate-900 hover:text-brand-700"
                        >
                          {candidate.title}
                        </Link>
                        <span className="pill bg-state-pending/10 text-state-pending ring-state-pending/20">
                          {similarityPercent(candidate.similarity)}% match
                        </span>
                        {typeof candidate.price === 'number' ? (
                          <span className="text-sm font-medium text-slate-700">
                            {currency(candidate.price)}
                          </span>
                        ) : null}
                      </div>

                      <p className="mt-1 text-xs text-slate-500">
                        {candidate.course_code}
                        {candidate.edition ? ` · ${candidate.edition}` : ''} · listed by{' '}
                        {candidate.uploader_name ?? 'another student'}
                      </p>
                      <p className="mt-0.5 text-xs text-slate-500">{candidate.reason}</p>
                    </li>
                  ))}
                </ul>

                <label className="mt-4 flex cursor-pointer items-start gap-2.5">
                  <input
                    type="checkbox"
                    checked={acknowledged}
                    aria-describedby={errors.acknowledge ? 'acknowledge-error' : undefined}
                    onChange={(event) => {
                      setAcknowledged(event.target.checked)
                      setErrors((current) => ({ ...current, acknowledge: undefined }))
                    }}
                    className="mt-0.5 h-4 w-4 rounded border-slate-300 text-brand-600 focus:ring-brand-600"
                  />
                  <span className="text-sm text-slate-700">
                    I&rsquo;ve checked — post anyway.
                  </span>
                </label>
                {errors.acknowledge ? (
                  <p id="acknowledge-error" className="field-error">
                    {errors.acknowledge}
                  </p>
                ) : null}
              </>
            )}
          </section>
        ) : null}

        {/* --------------------------------- FR 1.3 type + FR 3.1 pricing */}
        <section className="card space-y-4 p-6">
          <h2 className="text-base font-semibold text-slate-900">How are you listing it?</h2>

          <fieldset>
            <legend className="label">Listing type</legend>
            <div className="grid gap-2 sm:grid-cols-4">
              {LISTING_TYPES.map((type) => {
                const active = listingType === type
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
                      name="listing_type"
                      value={type}
                      checked={active}
                      onChange={() => setListingType(type)}
                      className="sr-only"
                    />
                    {listingTypeLabel(type)}
                  </label>
                )
              })}
            </div>
          </fieldset>

          {priced ? (
            <>
              <Input
                label={listingType === 'RENT' ? 'Rental price' : 'Asking price'}
                type="number"
                min={0}
                step="1"
                inputMode="numeric"
                required
                prefix="৳"
                value={price}
                error={errors.price}
                onChange={(event) => setPrice(event.target.value)}
              />

              {/* The suggestion always says what it is based on and how many
                  listings it saw. A bare number from a sample of one is the
                  failure mode this is designed around (section 8). */}
              {suggestionQuery.isFetching ? (
                <p className="flex items-center gap-2 text-sm text-slate-500">
                  <Spinner size="sm" label={null} />
                  Looking at comparable listings…
                </p>
              ) : suggestion ? (
                <div className="rounded-lg bg-brand-50 p-4 ring-1 ring-inset ring-brand-100">
                  {hasNumber ? (
                    <>
                      <p className="text-sm text-brand-900">
                        <span className="font-semibold">
                          {currency(suggestion.suggested)} suggested
                        </span>{' '}
                        · based on {pluralize(suggestion.sample_size, 'similar listing')}
                      </p>

                      {typeof suggestion.low === 'number' && typeof suggestion.high === 'number' ? (
                        <p className="mt-1 text-sm text-brand-800">
                          Most sit between {currency(suggestion.low)} and{' '}
                          {currency(suggestion.high)}.
                        </p>
                      ) : null}

                      {/* Always say which rung of the ladder the number came from. */}
                      <p className="mt-1 text-sm text-brand-800">{BASIS_NOTE[suggestion.basis]}</p>

                      <Button
                        variant="ghost"
                        size="sm"
                        className="mt-3"
                        onClick={() => setPrice(String(suggestion.suggested))}
                      >
                        Apply {currency(suggestion.suggested)}
                      </Button>
                    </>
                  ) : (
                    <>
                      <p className="text-sm font-semibold text-brand-900">
                        Not enough similar listings yet to suggest a price
                      </p>
                      <p className="mt-1 text-sm leading-relaxed text-brand-800">
                        {suggestion.sample_size > 0
                          ? `Only ${pluralize(suggestion.sample_size, 'listing')} matched — too few to average honestly. `
                          : 'Nothing comparable has been listed recently. '}
                        Price it at what you would pay for it.
                      </p>
                    </>
                  )}
                </div>
              ) : suggestionQuery.isError ? (
                <p className="text-sm text-slate-500">
                  The price suggestion is unavailable right now — set your own price.
                </p>
              ) : null}
            </>
          ) : (
            <p className="rounded-lg bg-slate-50 px-3 py-2.5 text-sm leading-relaxed text-slate-600 ring-1 ring-inset ring-slate-200">
              {listingType === 'FREE'
                ? 'Free listings carry no price — anyone can request it.'
                : 'Exchange listings carry no price — you agree the swap in chat.'}
            </p>
          )}
        </section>

        <div className="flex flex-wrap items-center gap-3">
          <Button
            type="submit"
            loading={upload.isPending}
            disabled={submitBlocked}
            title={
              submitBlocked ? 'Confirm you have checked the similar listings first' : undefined
            }
          >
            Upload for review
          </Button>
          <Link to="/my-listings" className="btn btn-ghost">
            Cancel
          </Link>
          <p className="text-xs text-slate-500">
            You keep the file — the original is released only when a transaction completes.
          </p>
        </div>
      </form>
    </div>
  )
}
