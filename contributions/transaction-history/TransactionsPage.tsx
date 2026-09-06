import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { keepPreviousData, useQuery } from '@tanstack/react-query'

import { EmptyState } from '../../components/EmptyState'
import { ErrorBox } from '../../components/ErrorBox'
import { Pagination } from '../../components/Pagination'
import { ListingTypePill } from '../../components/Pill'
import { PageSpinner } from '../../components/Spinner'
import { StatusPill } from '../../components/StatusPill'
import { api } from '../../lib/api'
import { cx, formatDate, formatPrice } from '../../lib/format'
import type { Page, Transaction, TransactionQuery } from '../../lib/types'

/**
 * FR 3.4 -- transaction history across buys, sells, rentals and exchanges.
 * One endpoint, `GET /transactions/me`; the tabs are query parameters.
 */

const PAGE_SIZE = 20

type TabKey = 'all' | 'buying' | 'selling' | 'completed'

const TABS: { key: TabKey; label: string; hint: string }[] = [
  { key: 'all', label: 'All', hint: 'Everything you have been part of' },
  { key: 'buying', label: 'Buying', hint: 'Where you are the buyer or renter' },
  { key: 'selling', label: 'Selling', hint: 'Where you are the uploader' },
  { key: 'completed', label: 'Completed', hint: 'Handed over and confirmed by both sides' },
]

function queryForTab(tab: TabKey, page: number): TransactionQuery {
  const base: TransactionQuery = { page, page_size: PAGE_SIZE }
  switch (tab) {
    case 'buying':
      return { ...base, role: 'buyer' }
    case 'selling':
      return { ...base, role: 'seller' }
    case 'completed':
      return { ...base, status: 'COMPLETED' }
    default:
      return base
  }
}

export default function TransactionsPage() {
  const [tab, setTab] = useState<TabKey>('all')
  const [page, setPage] = useState(1)

  const params = useMemo(() => queryForTab(tab, page), [tab, page])

  const transactionsQuery = useQuery({
    queryKey: ['transactions', 'me', params],
    queryFn: () => api.get<Page<Transaction>>('/transactions/me', { query: params }),
    placeholderData: keepPreviousData,
  })

  const items = transactionsQuery.data?.items ?? []
  const activeTab = TABS.find((entry) => entry.key === tab) ?? TABS[0]

  const selectTab = (key: TabKey): void => {
    setTab(key)
    setPage(1)
  }

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-xl font-semibold tracking-tight text-slate-900">Transactions</h1>
        <p className="mt-1.5 max-w-2xl text-sm leading-relaxed text-slate-500">
          Every exchange you have started or accepted, from the first request through to the QR
          handoff.
        </p>
      </header>

      <div className="flex flex-wrap items-center gap-1 border-b border-slate-200">
        {TABS.map((entry) => (
          <button
            key={entry.key}
            type="button"
            aria-current={entry.key === tab ? 'page' : undefined}
            onClick={() => selectTab(entry.key)}
            className={cx(
              '-mb-px border-b-2 px-3 py-2 text-sm font-medium transition-colors',
              entry.key === tab
                ? 'border-brand-600 text-brand-700'
                : 'border-transparent text-slate-500 hover:border-slate-300 hover:text-slate-800',
            )}
          >
            {entry.label}
          </button>
        ))}
      </div>

      {transactionsQuery.isPending ? (
        <PageSpinner label="Loading your transactions" />
      ) : transactionsQuery.isError ? (
        <ErrorBox
          error={transactionsQuery.error}
          onRetry={() => void transactionsQuery.refetch()}
        />
      ) : items.length === 0 ? (
        <EmptyState
          title={tab === 'all' ? 'No transactions yet' : `Nothing under “${activeTab.label}”`}
          description={
            tab === 'all'
              ? 'Find a listing you need and use “Request this item”. It shows up here as soon as the request is sent.'
              : activeTab.hint
          }
          action={
            <Link to="/" className="btn btn-primary">
              Browse materials
            </Link>
          }
        />
      ) : (
        <>
          <div className="card overflow-hidden">
            {/* Column headings only make sense once there is room for them. */}
            <div className="hidden grid-cols-[minmax(0,2.2fr)_minmax(0,1.2fr)_minmax(0,0.9fr)_minmax(0,0.8fr)_minmax(0,1fr)_minmax(0,0.8fr)] gap-4 border-b border-slate-100 bg-slate-50/70 px-4 py-2.5 text-xs font-semibold uppercase tracking-wide text-slate-500 lg:grid">
              <span>Listing</span>
              <span>Counterparty</span>
              <span>Type</span>
              <span>Price</span>
              <span>Status</span>
              <span className="text-right">Date</span>
            </div>

            <ul className="divide-y divide-slate-100">
              {items.map((transaction) => {
                // `role` is computed server-side -- never compare ids here.
                const role = transaction.role
                const counterparty =
                  role === 'buyer'
                    ? (transaction.seller_name ?? 'Seller')
                    : (transaction.buyer_name ?? 'Buyer')

                return (
                  <li key={transaction.transaction_id}>
                    <Link
                      to={`/transactions/${transaction.transaction_id}`}
                      className="grid gap-2 px-4 py-3.5 transition-colors hover:bg-slate-50 lg:grid-cols-[minmax(0,2.2fr)_minmax(0,1.2fr)_minmax(0,0.9fr)_minmax(0,0.8fr)_minmax(0,1fr)_minmax(0,0.8fr)] lg:items-center lg:gap-4"
                    >
                      <span className="min-w-0">
                        <span className="block truncate text-sm font-semibold text-slate-900">
                          {transaction.listing_title ?? 'Untitled listing'}
                        </span>
                        <span className="mt-0.5 block text-xs text-slate-400 lg:hidden">
                          {role === 'buyer' ? 'Buying from' : 'Selling to'} {counterparty}
                        </span>
                      </span>

                      <span className="hidden min-w-0 truncate text-sm text-slate-600 lg:block">
                        <span className="text-xs uppercase tracking-wide text-slate-400">
                          {role === 'buyer' ? 'from ' : 'to '}
                        </span>
                        {counterparty}
                      </span>

                      <span>
                        <ListingTypePill type={transaction.transaction_type} />
                      </span>

                      <span className="text-sm font-medium text-slate-900">
                        {formatPrice(transaction.transaction_type, transaction.agreed_price)}
                      </span>

                      <span>
                        <StatusPill status={transaction.status} kind="transaction" />
                      </span>

                      <span className="text-xs text-slate-500 lg:text-right">
                        {formatDate(transaction.transaction_date)}
                      </span>
                    </Link>
                  </li>
                )
              })}
            </ul>
          </div>

          <Pagination
            page={transactionsQuery.data?.page ?? page}
            pages={transactionsQuery.data?.pages ?? 1}
            total={transactionsQuery.data?.total}
            pageSize={transactionsQuery.data?.page_size}
            onPageChange={setPage}
          />
        </>
      )}
    </div>
  )
}
