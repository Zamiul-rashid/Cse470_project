import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Html5Qrcode } from 'html5-qrcode'

import { Button, type ButtonVariant } from '../../components/Button'
import { ErrorBox } from '../../components/ErrorBox'
import { Input } from '../../components/Input'
import { Modal } from '../../components/Modal'
import { ListingTypePill } from '../../components/Pill'
import { PageSpinner } from '../../components/Spinner'
import { StarRating } from '../../components/StarRating'
import { StatusPill } from '../../components/StatusPill'
import { TextArea } from '../../components/TextArea'
import { useToast } from '../../components/Toast'
import { api } from '../../lib/api'
import { cx, dueLabel, formatDateTime, formatPrice, parseDate } from '../../lib/format'
import type {
  QRGenerateResponse,
  QRVerifyRequest,
  Review,
  ReviewCreate,
  Transaction,
  TransactionRole,
  TransactionStatus,
  TransactionStatusUpdate,
} from '../../lib/types'

/**
 * FR 3.3 + FR 2.5 -- one transaction: its state machine, the QR handoff, and
 * the review that a completed handoff unlocks.
 *
 * The handoff is deliberately two-sided: the seller
 * generates a signed, short-lived, single-use code; the buyer submits it; only
 * when both flags are set does the transaction complete.
 *
 * Two different shapes are in play. `TransactionRead.handoff` is the shared,
 * credential-free state (`generated`, `expires_at`, the two verified flags) and
 * both parties see it. `QRGenerateResponse` is the seller's one-off copy of the
 * actual credentials -- the PNG and the manual code -- which the server never
 * repeats, so it lives in component state and nowhere else.
 */

const STEPS: { status: TransactionStatus; label: string; description: string }[] = [
  { status: 'REQUESTED', label: 'Requested', description: 'The buyer asked for this material.' },
  { status: 'ACCEPTED', label: 'Accepted', description: 'The seller agreed to the exchange.' },
  {
    status: 'AWAITING_HANDOFF',
    label: 'Awaiting handoff',
    description: 'Meet up, then verify with the QR code or the 6-digit code.',
  },
  { status: 'COMPLETED', label: 'Completed', description: 'Both sides confirmed the handoff.' },
]

interface StatusAction {
  label: string
  next: TransactionStatus
  variant: ButtonVariant
}

function actionsFor(status: TransactionStatus, role: TransactionRole): StatusAction[] {
  switch (status) {
    case 'REQUESTED':
      return role === 'seller'
        ? [
            { label: 'Accept request', next: 'ACCEPTED', variant: 'primary' },
            { label: 'Decline', next: 'CANCELLED', variant: 'ghost' },
          ]
        : [{ label: 'Cancel my request', next: 'CANCELLED', variant: 'ghost' }]
    case 'ACCEPTED':
      return [
        { label: 'Ready to hand over', next: 'AWAITING_HANDOFF', variant: 'primary' },
        { label: 'Cancel', next: 'CANCELLED', variant: 'ghost' },
      ]
    case 'AWAITING_HANDOFF':
      return [{ label: 'Cancel', next: 'CANCELLED', variant: 'ghost' }]
    default:
      return []
  }
}

function countdown(totalSeconds: number): string {
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  return `${minutes}:${String(seconds).padStart(2, '0')}`
}

// ---------------------------------------------------------------------------
// timeline
// ---------------------------------------------------------------------------

function Timeline({ status }: { status: TransactionStatus }) {
  const cancelled = status === 'CANCELLED'
  const currentIndex = cancelled ? -1 : STEPS.findIndex((step) => step.status === status)

  return (
    <ol className="space-y-0">
      {STEPS.map((step, index) => {
        const done = !cancelled && index < currentIndex
        const current = !cancelled && index === currentIndex
        const last = index === STEPS.length - 1

        return (
          <li key={step.status} className="flex gap-3">
            <div className="flex flex-col items-center">
              <span
                aria-hidden="true"
                className={cx(
                  'flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-[11px] font-semibold ring-2',
                  done && 'bg-state-completed text-white ring-state-completed',
                  current && 'bg-white text-brand-700 ring-brand-600',
                  !done && !current && 'bg-white text-slate-400 ring-slate-200',
                )}
              >
                {done ? '✓' : index + 1}
              </span>
              {last ? null : (
                <span
                  aria-hidden="true"
                  className={cx('my-1 w-0.5 flex-1', done ? 'bg-state-completed' : 'bg-slate-200')}
                />
              )}
            </div>

            <div className={cx('pb-6', last && 'pb-0')}>
              <p
                className={cx(
                  'text-sm font-semibold',
                  current ? 'text-brand-700' : done ? 'text-slate-900' : 'text-slate-400',
                )}
              >
                {step.label}
                {current ? <span className="ml-2 text-xs font-medium">· current</span> : null}
              </p>
              <p className="mt-0.5 text-xs leading-relaxed text-slate-500">{step.description}</p>
            </div>
          </li>
        )
      })}
    </ol>
  )
}

// ---------------------------------------------------------------------------
// camera scanner (buyer side)
// ---------------------------------------------------------------------------

const SCANNER_ELEMENT_ID = 'notevault-qr-reader'

function QrScanner({
  onResult,
  onFailure,
}: {
  onResult: (text: string) => void
  onFailure: (message: string) => void
}) {
  // Handlers live in a ref so a re-render never tears the camera down.
  const handlers = useRef({ onResult, onFailure })
  handlers.current = { onResult, onFailure }

  useEffect(() => {
    const scanner = new Html5Qrcode(SCANNER_ELEMENT_ID)
    let finished = false

    const stop = (): Promise<void> => scanner.stop().catch(() => undefined)

    scanner
      .start(
        { facingMode: 'environment' },
        { fps: 10, qrbox: { width: 240, height: 240 } },
        (decodedText: string) => {
          if (finished) return
          finished = true
          void stop().then(() => handlers.current.onResult(decodedText))
        },
        undefined,
      )
      .catch((error: unknown) => {
        handlers.current.onFailure(
          error instanceof Error
            ? error.message
            : 'The camera could not be started on this device.',
        )
      })

    return () => {
      if (finished) return
      finished = true
      void stop()
    }
  }, [])

  return <div id={SCANNER_ELEMENT_ID} className="w-full overflow-hidden rounded-xl bg-slate-900" />
}

// ---------------------------------------------------------------------------
// page
// ---------------------------------------------------------------------------

export default function TransactionDetailPage() {
  const { id = '' } = useParams<{ id: string }>()
  const queryClient = useQueryClient()
  const toast = useToast()

  /** Seller only, and only for as long as this page stays mounted. */
  const [credentials, setCredentials] = useState<QRGenerateResponse | null>(null)
  const [now, setNow] = useState(() => Date.now())
  const [manualCode, setManualCode] = useState('')
  const [scannerOpen, setScannerOpen] = useState(false)
  const [rating, setRating] = useState(0)
  const [comment, setComment] = useState('')

  const transactionQuery = useQuery({
    queryKey: ['transactions', id],
    queryFn: () => api.get<Transaction>(`/transactions/${id}`),
    enabled: Boolean(id),
  })

  const transaction = transactionQuery.data ?? null

  const invalidate = useCallback((): void => {
    void queryClient.invalidateQueries({ queryKey: ['transactions'] })
  }, [queryClient])

  const updateStatus = useMutation({
    mutationFn: (next: TransactionStatus) => {
      const payload: TransactionStatusUpdate = { status: next }
      return api.patch<Transaction>(`/transactions/${id}/status`, payload)
    },
    onSuccess: (updated) => {
      invalidate()
      toast.success('Transaction updated', `It is now ${updated.status.toLowerCase()}.`)
    },
    onError: (error: unknown) => toast.fromError(error, 'That transition was refused'),
  })

  const generateHandoff = useMutation({
    mutationFn: () => api.post<QRGenerateResponse>(`/transactions/${id}/qr`),
    onSuccess: (created) => {
      setCredentials(created)
      setNow(Date.now())
      invalidate()
    },
    onError: (error: unknown) => toast.fromError(error, 'Could not generate a handoff code'),
  })

  const verifyHandoff = useMutation({
    // The verify route answers with the whole transaction, handoff flags included.
    mutationFn: (payload: QRVerifyRequest | undefined) =>
      api.post<Transaction>(`/transactions/${id}/qr/verify`, payload),
    onSuccess: (updated) => {
      queryClient.setQueryData(['transactions', id], updated)
      setManualCode('')
      setScannerOpen(false)
      invalidate()
      toast.success(
        updated.status === 'COMPLETED' ? 'Handoff complete' : 'Your side is confirmed',
        updated.status === 'COMPLETED'
          ? 'Both sides confirmed — this transaction is done.'
          : 'The other side still has to confirm on their own device.',
      )
    },
    onError: (error: unknown) => toast.fromError(error, 'That code was not accepted'),
  })

  const submitReview = useMutation({
    mutationFn: (payload: ReviewCreate) => api.post<Review>('/reviews', payload),
    onSuccess: () => {
      invalidate()
      setRating(0)
      setComment('')
      toast.success('Review posted', 'Thanks — it feeds into their average rating.')
    },
    onError: (error: unknown) => toast.fromError(error, 'Could not post the review'),
  })

  // A live countdown is the whole point of a 10-minute token.
  useEffect(() => {
    if (!credentials) return
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [credentials])

  const handleScan = (token: string): void => {
    verifyHandoff.mutate({ token })
  }

  const handleScanFailure = (message: string): void => {
    setScannerOpen(false)
    toast.error('Camera unavailable', `${message} Use the 6-digit code instead.`)
  }

  if (transactionQuery.isPending) return <PageSpinner label="Loading this transaction" />
  if (transactionQuery.isError) {
    return (
      <ErrorBox
        error={transactionQuery.error}
        onRetry={() => void transactionQuery.refetch()}
        title="This transaction could not be loaded"
      />
    )
  }
  if (!transaction) return <PageSpinner label="Loading this transaction" />

  // Which side of the deal the caller is on is decided server-side (`role`).
  const role: TransactionRole = transaction.role

  const counterparty =
    role === 'buyer'
      ? { id: transaction.seller_id, name: transaction.seller_name ?? 'Seller', label: 'Seller' }
      : { id: transaction.buyer_id, name: transaction.buyer_name ?? 'Buyer', label: 'Buyer' }

  const actions = actionsFor(transaction.status, role)
  const handoffPhase =
    transaction.status === 'ACCEPTED' || transaction.status === 'AWAITING_HANDOFF'

  /** The shared, credential-free view of the handoff. Null until one exists. */
  const handoff = transaction.handoff ?? null

  const expiresAt = parseDate(credentials?.expires_at ?? handoff?.expires_at)?.getTime() ?? null
  const secondsLeft = expiresAt === null ? null : Math.max(0, Math.round((expiresAt - now) / 1000))
  const expired = secondsLeft !== null && secondsLeft === 0

  /** Server-computed gate: COMPLETED, caller is a party, caller has not reviewed. */
  const canReview = transaction.can_review

  const handleReview = (event: FormEvent<HTMLFormElement>): void => {
    event.preventDefault()
    if (rating < 1) return
    submitReview.mutate({
      transaction_id: transaction.transaction_id,
      rating,
      comment: comment.trim() ? comment.trim() : null,
    })
  }

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <Link to="/transactions" className="text-xs font-medium text-brand-700 hover:underline">
            ← All transactions
          </Link>
          <h1 className="mt-1.5 truncate text-xl font-semibold tracking-tight text-slate-900">
            {transaction.listing_title ?? 'Transaction'}
          </h1>
          <p className="mt-1 text-sm text-slate-500">
            You are the {role} · started {formatDateTime(transaction.transaction_date)}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <ListingTypePill type={transaction.transaction_type} />
          <StatusPill status={transaction.status} kind="transaction" />
        </div>
      </header>

      {transaction.status === 'CANCELLED' ? (
        <div className="rounded-xl bg-state-removed/10 p-4 text-sm text-slate-700 ring-1 ring-inset ring-state-removed/20">
          This transaction was cancelled. The listing is back in circulation — you can request it
          again from its page.
        </div>
      ) : null}

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_20rem]">
        <div className="space-y-6">
          {/* ---- state machine ------------------------------------------ */}
          <section className="card p-5">
            <h2 className="mb-4 text-sm font-semibold text-slate-900">Progress</h2>
            <Timeline status={transaction.status} />

            {actions.length > 0 ? (
              <div className="mt-5 flex flex-wrap gap-2 border-t border-slate-100 pt-4">
                {actions.map((action) => (
                  <Button
                    key={action.next + action.label}
                    variant={action.variant}
                    loading={updateStatus.isPending && updateStatus.variables === action.next}
                    disabled={updateStatus.isPending}
                    onClick={() => updateStatus.mutate(action.next)}
                  >
                    {action.label}
                  </Button>
                ))}
              </div>
            ) : null}
          </section>

          {/* ---- handoff (FR 3.3) --------------------------------------- */}
          {handoffPhase ? (
            <section className="card p-5">
              <h2 className="text-sm font-semibold text-slate-900">Handoff verification</h2>
              <p className="mt-1 text-sm leading-relaxed text-slate-500">
                The seller generates a code; the buyer submits it. The transaction completes only
                once both sides have confirmed.
              </p>

              {role === 'seller' ? (
                <div className="mt-5 space-y-5">
                  {!credentials ? (
                    <div className="rounded-xl bg-slate-50 p-6 text-center ring-1 ring-inset ring-slate-200">
                      <p className="text-sm text-slate-600">
                        {handoff?.generated
                          ? 'A code was generated for this handoff, but it is only ever shown once. Generate a fresh one when you are standing with the buyer.'
                          : 'Generate a code when you are standing with the buyer. It is valid for a few minutes and can only be used once.'}
                      </p>
                      <Button
                        className="mt-4"
                        loading={generateHandoff.isPending}
                        onClick={() => generateHandoff.mutate()}
                      >
                        {handoff?.generated ? 'Generate a new code' : 'Generate handoff code'}
                      </Button>
                    </div>
                  ) : (
                    <div className="flex flex-col items-center rounded-xl bg-white p-4 ring-1 ring-inset ring-slate-200">
                      <img
                        src={credentials.qr_png_data_uri}
                        alt="Handoff QR code for the buyer to scan"
                        className={cx(
                          'h-64 w-64 max-w-full rounded-lg',
                          expired && 'opacity-30 grayscale',
                        )}
                      />

                      <p className="mt-5 text-xs font-semibold uppercase tracking-wide text-slate-500">
                        Or read this out
                      </p>
                      <p className="mt-1 font-mono text-4xl font-bold tracking-[0.35em] text-slate-900">
                        {credentials.manual_code}
                      </p>

                      <p
                        className={cx(
                          'mt-3 text-sm font-medium tabular-nums',
                          expired ? 'text-state-rejected' : 'text-slate-600',
                        )}
                      >
                        {expired
                          ? 'Expired — generate a new code'
                          : `Expires in ${countdown(secondsLeft ?? 0)}`}
                      </p>

                      <Button
                        variant="ghost"
                        size="sm"
                        className="mt-3"
                        loading={generateHandoff.isPending}
                        onClick={() => generateHandoff.mutate()}
                      >
                        {expired ? 'Generate a new code' : 'Regenerate'}
                      </Button>
                    </div>
                  )}

                  {credentials || handoff?.generated ? (
                    <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl bg-slate-50 p-4 ring-1 ring-inset ring-slate-200">
                      <div className="text-sm text-slate-600">
                        <p className="font-medium text-slate-900">
                          Buyer confirmed: {handoff?.verified_by_buyer ? 'yes' : 'not yet'}
                        </p>
                        <p className="mt-0.5">
                          {handoff?.verified_at
                            ? `Handoff verified ${formatDateTime(handoff.verified_at)}.`
                            : 'Confirm your own side once the material has changed hands.'}
                        </p>
                      </div>
                      <Button
                        disabled={handoff?.verified_by_seller ?? false}
                        loading={verifyHandoff.isPending}
                        onClick={() => verifyHandoff.mutate(undefined)}
                      >
                        {handoff?.verified_by_seller ? 'You have confirmed' : "I've handed it over"}
                      </Button>
                    </div>
                  ) : null}
                </div>
              ) : (
                <div className="mt-5 space-y-4">
                  {handoff?.verified_by_buyer ? null : (
                    <div className="rounded-xl bg-slate-50 p-4 ring-1 ring-inset ring-slate-200">
                      <p className="text-sm font-medium text-slate-900">Enter the 6-digit code</p>
                      <p className="mt-1 text-xs leading-relaxed text-slate-500">
                        The camera needs a secure context, so scanning will not start over a LAN
                        address like 192.168.0.4 — typing the code always works.
                      </p>

                      <form
                        className="mt-3 flex flex-wrap items-end gap-2"
                        onSubmit={(event) => {
                          event.preventDefault()
                          if (manualCode.trim().length !== 6) return
                          verifyHandoff.mutate({ manual_code: manualCode.trim() })
                        }}
                      >
                        <Input
                          aria-label="Six-digit handoff code"
                          inputMode="numeric"
                          autoComplete="one-time-code"
                          maxLength={6}
                          placeholder="000000"
                          value={manualCode}
                          onChange={(event) =>
                            setManualCode(event.target.value.replace(/\D/g, '').slice(0, 6))
                          }
                          containerClassName="w-40"
                          className="text-center font-mono text-xl tracking-[0.3em]"
                        />
                        <Button
                          type="submit"
                          disabled={manualCode.trim().length !== 6}
                          loading={verifyHandoff.isPending}
                        >
                          Confirm handoff
                        </Button>
                        <Button variant="ghost" onClick={() => setScannerOpen(true)}>
                          Scan instead
                        </Button>
                      </form>
                    </div>
                  )}

                  {handoff?.verified_by_buyer && !handoff.verified_by_seller ? (
                    <p className="rounded-xl bg-state-approved/10 px-4 py-3 text-sm text-slate-700 ring-1 ring-inset ring-state-approved/20">
                      Your side is confirmed
                      {handoff.verified_at ? ` (${formatDateTime(handoff.verified_at)})` : ''}.
                      Waiting for {counterparty.name} to confirm the handoff on their device.
                    </p>
                  ) : null}
                </div>
              )}
            </section>
          ) : null}

          {/* ---- review (FR 2.5) ---------------------------------------- */}
          {transaction.status === 'COMPLETED' ? (
            <section className="card p-5">
              <h2 className="text-sm font-semibold text-slate-900">
                Rate {counterparty.name}
              </h2>

              {canReview ? (
                <form onSubmit={handleReview} className="mt-4 space-y-4">
                  <div>
                    <p className="label">Your rating</p>
                    <StarRating
                      value={rating}
                      onChange={setRating}
                      size="lg"
                      showValue
                      label={`Rate ${counterparty.name}`}
                    />
                  </div>

                  <TextArea
                    label="Comment"
                    value={comment}
                    maxLength={500}
                    showCount
                    onChange={(event) => setComment(event.target.value)}
                    placeholder="Was the material as described? Did they turn up on time?"
                    hint="Optional, but it is what makes the rating useful to the next student."
                  />

                  <Button type="submit" disabled={rating < 1} loading={submitReview.isPending}>
                    Post review
                  </Button>
                </form>
              ) : (
                <p className="mt-2 text-sm text-slate-500">
                  You have already reviewed this transaction. Thanks — reviews are what keep the
                  ratings honest.
                </p>
              )}
            </section>
          ) : null}
        </div>

        {/* ---- summary --------------------------------------------------- */}
        <aside className="space-y-4">
          <section className="card p-5">
            <h2 className="text-sm font-semibold text-slate-900">Details</h2>
            <dl className="mt-3 space-y-3 text-sm">
              <div>
                <dt className="text-xs uppercase tracking-wide text-slate-400">Listing</dt>
                <dd className="mt-0.5">
                  <Link
                    to={`/materials/${transaction.listing_id}`}
                    className="font-medium text-brand-700 hover:underline"
                  >
                    {transaction.listing_title ?? 'View listing'}
                  </Link>
                </dd>
              </div>

              <div>
                <dt className="text-xs uppercase tracking-wide text-slate-400">
                  {counterparty.label}
                </dt>
                <dd className="mt-0.5">
                  <Link
                    to={`/users/${counterparty.id}`}
                    className="font-medium text-slate-800 hover:text-brand-700"
                  >
                    {counterparty.name}
                  </Link>
                </dd>
              </div>

              <div>
                <dt className="text-xs uppercase tracking-wide text-slate-400">Agreed price</dt>
                <dd className="mt-0.5 font-semibold text-slate-900">
                  {formatPrice(transaction.transaction_type, transaction.agreed_price)}
                </dd>
              </div>

              <div>
                <dt className="text-xs uppercase tracking-wide text-slate-400">Requested</dt>
                <dd className="mt-0.5 text-slate-700">
                  {formatDateTime(transaction.transaction_date)}
                </dd>
              </div>

              {transaction.completed_at ? (
                <div>
                  <dt className="text-xs uppercase tracking-wide text-slate-400">Completed</dt>
                  <dd className="mt-0.5 text-slate-700">
                    {formatDateTime(transaction.completed_at)}
                  </dd>
                </div>
              ) : null}
            </dl>
          </section>

          {transaction.rental ? (
            <section className="card p-5">
              <h2 className="text-sm font-semibold text-slate-900">Rental</h2>
              <p className="mt-2 text-sm text-slate-700">{dueLabel(transaction.rental.due_date)}</p>
              <p className="mt-1 text-xs text-slate-500">
                {transaction.rental.returned ? 'Returned' : 'Not yet returned'}
              </p>
              <Link to="/rentals" className="btn btn-ghost mt-3 w-full">
                Manage rentals
              </Link>
            </section>
          ) : null}
        </aside>
      </div>

      {/* ---- scanner ----------------------------------------------------- */}
      <Modal
        open={scannerOpen}
        onClose={() => setScannerOpen(false)}
        title="Scan the seller's code"
        description="Point the camera at the QR code on the seller's screen."
        size="md"
      >
        <div className="space-y-3">
          {scannerOpen ? <QrScanner onResult={handleScan} onFailure={handleScanFailure} /> : null}
          <p className="text-xs leading-relaxed text-slate-500">
            Nothing happening? Camera access is blocked outside HTTPS and localhost. Close this and
            type the 6-digit code instead — it does exactly the same thing.
          </p>
        </div>
      </Modal>
    </div>
  )
}
