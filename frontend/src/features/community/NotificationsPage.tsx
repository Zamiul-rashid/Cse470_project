import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { Button } from '../../components/Button'
import { EmptyState } from '../../components/EmptyState'
import { ErrorBox } from '../../components/ErrorBox'
import { Pagination } from '../../components/Pagination'
import { PageSpinner } from '../../components/Spinner'
import { useToast } from '../../components/Toast'
import { ApiError, api } from '../../lib/api'
import { cx, notificationLabel, parseDate, relativeTime } from '../../lib/format'
import type { Notification, Page as PageEnvelope } from '../../lib/types'
import { notificationKeys } from '../../lib/ws'

/**
 * FR 2.3 -- the durable notification list.
 *
 * Every notification is a row first and a socket push second, so this page is
 * the source of truth: whatever arrived while the
 * user was offline is here. `ref_type` / `ref_id` say where a row leads.
 */

const PAGE_SIZE = 20

/** Where a notification points. */
function notificationTarget(item: Notification): string | null {
  if (!item.ref_id) return null
  switch (item.ref_type) {
    case 'listing':
      return `/materials/${item.ref_id}`
    case 'transaction':
      return `/transactions/${item.ref_id}`
    case 'conversation':
      return `/messages/${item.ref_id}`
    case 'request':
      return '/requests'
    default:
      return null
  }
}

function targetLabel(item: Notification): string | null {
  switch (item.ref_type) {
    case 'listing':
      return 'View listing'
    case 'transaction':
      return 'View transaction'
    case 'conversation':
      return 'Open conversation'
    case 'request':
      return 'Go to request board'
    default:
      return null
  }
}

/** Unread first, then newest first inside each group. */
function sortForDisplay(items: Notification[]): Notification[] {
  return [...items].sort((a, b) => {
    if (a.is_read !== b.is_read) return a.is_read ? 1 : -1
    return (parseDate(b.created_at)?.getTime() ?? 0) - (parseDate(a.created_at)?.getTime() ?? 0)
  })
}

export default function NotificationsPage() {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const toast = useToast()
  const [page, setPage] = useState(1)

  const listQuery = useQuery({
    queryKey: notificationKeys.list({ page, page_size: PAGE_SIZE }),
    queryFn: () =>
      api.get<PageEnvelope<Notification>>('/notifications', {
        query: { page, page_size: PAGE_SIZE },
      }),
    placeholderData: keepPreviousData,
  })

  const markRead = useMutation({
    mutationFn: (notificationId: string) => api.post<void>(`/notifications/${notificationId}/read`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: notificationKeys.all }),
  })

  const markAllRead = useMutation({
    mutationFn: async (unread: Notification[]): Promise<void> => {
      try {
        await api.post<void>('/notifications/read-all')
        return
      } catch (error) {
        // Older builds only expose the per-notification route; walk the page.
        const missing = error instanceof ApiError && (error.status === 404 || error.status === 405)
        if (!missing) throw error
      }
      await Promise.all(
        unread.map((item) => api.post<void>(`/notifications/${item.notification_id}/read`)),
      )
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: notificationKeys.all })
      toast.success('All caught up')
    },
    onError: (error: unknown) => toast.fromError(error, 'Could not mark everything read'),
  })

  const items = useMemo(() => sortForDisplay(listQuery.data?.items ?? []), [listQuery.data])
  // `Page` carries no unread total, so the count is what this page can see --
  // which is also exactly the set "Mark all read" falls back to walking.
  const unread = items.filter((item) => !item.is_read)

  const open = (item: Notification): void => {
    if (!item.is_read) markRead.mutate(item.notification_id)
    const target = notificationTarget(item)
    if (target) navigate(target)
  }

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-slate-900">Notifications</h1>
          <p className="mt-1.5 max-w-2xl text-sm leading-relaxed text-slate-500">
            Request matches, new messages, moderation results and rental reminders. Unread first.
          </p>
        </div>
        <Button
          variant="ghost"
          disabled={unread.length === 0}
          loading={markAllRead.isPending}
          onClick={() => markAllRead.mutate(unread)}
        >
          Mark all read
        </Button>
      </header>

      {listQuery.isPending ? (
        <PageSpinner label="Loading your notifications" />
      ) : listQuery.isError ? (
        <ErrorBox error={listQuery.error} onRetry={() => void listQuery.refetch()} />
      ) : items.length === 0 ? (
        <EmptyState
          title="Nothing to catch up on"
          description="Post a request or save a wishlist item, and matches will land here the moment a listing is approved."
        />
      ) : (
        <>
          <ul className="card divide-y divide-slate-100 overflow-hidden">
            {items.map((item) => {
              const label = targetLabel(item)
              return (
                <li key={item.notification_id}>
                  <button
                    type="button"
                    onClick={() => open(item)}
                    className={cx(
                      'flex w-full gap-3 px-4 py-3.5 text-left transition-colors hover:bg-slate-50',
                      !item.is_read && 'bg-brand-50/50',
                    )}
                  >
                    <span
                      aria-hidden="true"
                      className={cx(
                        'mt-2 h-2 w-2 shrink-0 rounded-full',
                        item.is_read ? 'bg-slate-200' : 'bg-brand-600',
                      )}
                    />
                    <span className="min-w-0 flex-1">
                      <span className="flex flex-wrap items-baseline gap-x-2">
                        <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                          {notificationLabel(item.type)}
                        </span>
                        <span className="text-xs text-slate-400">
                          {relativeTime(item.created_at)}
                        </span>
                        {item.is_read ? null : (
                          <span className="text-xs font-medium text-brand-700">New</span>
                        )}
                      </span>
                      <span
                        className={cx(
                          'mt-1 block text-sm leading-relaxed',
                          item.is_read ? 'text-slate-600' : 'text-slate-900',
                        )}
                      >
                        {item.message}
                      </span>
                      {label ? (
                        <span className="mt-1.5 block text-xs font-medium text-brand-700">
                          {label} →
                        </span>
                      ) : null}
                    </span>
                  </button>
                </li>
              )
            })}
          </ul>

          <Pagination
            page={listQuery.data?.page ?? page}
            pages={listQuery.data?.pages ?? 1}
            total={listQuery.data?.total}
            pageSize={listQuery.data?.page_size}
            onPageChange={setPage}
          />
        </>
      )}
    </div>
  )
}
