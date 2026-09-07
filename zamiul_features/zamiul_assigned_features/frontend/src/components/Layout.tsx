import { Suspense, useEffect, useRef, useState, type RefObject } from 'react'
import { Link, NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '../lib/api'
import { useAuth, useCampusFilter } from '../lib/auth'
import { cx, initials, notificationLabel, relativeTime } from '../lib/format'
import type { Campus, Notification, Page, UnreadCountResponse } from '../lib/types'
import { notificationKeys, useNotificationSocket } from '../lib/ws'
import { PageSpinner } from './Spinner'
import { useToast } from './Toast'

/** Primary navigation. Everything else hangs off the account menu. */
const NAV_ITEMS = [
  { to: '/', label: 'Browse', end: true },
  { to: '/requests', label: 'Requests', end: false },
  { to: '/messages', label: 'Messages', end: false },
  { to: '/my-listings', label: 'My listings', end: false },
  { to: '/transactions', label: 'Transactions', end: false },
] as const

const ACCOUNT_LINKS = [
  { to: '/profile', label: 'Profile' },
  { to: '/wishlist', label: 'Wishlist' },
  { to: '/rentals', label: 'My rentals' },
  { to: '/notifications', label: 'All notifications' },
  { to: '/leaderboard', label: 'Leaderboard' },
  { to: '/trending', label: 'Trending courses' },
] as const

/** Where a notification's ref_type / ref_id points. */
function notificationTarget(item: Notification): string {
  if (!item.ref_id) return '/notifications'
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
      return '/notifications'
  }
}

/** Closes a popover when the user clicks anywhere outside it. */
function useDismissOnOutsideClick(
  ref: RefObject<HTMLElement>,
  open: boolean,
  onDismiss: () => void,
): void {
  useEffect(() => {
    if (!open) return
    const onPointerDown = (event: MouseEvent): void => {
      if (ref.current && !ref.current.contains(event.target as Node)) onDismiss()
    }
    const onKeyDown = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') onDismiss()
    }
    document.addEventListener('mousedown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('mousedown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [ref, open, onDismiss])
}

const navLinkClasses = ({ isActive }: { isActive: boolean }): string =>
  cx(
    'rounded-lg px-3 py-1.5 text-sm font-medium transition-colors',
    isActive ? 'bg-brand-50 text-brand-700' : 'text-slate-600 hover:bg-slate-100 hover:text-slate-900',
  )

/**
 * The application shell: header, primary nav, campus scope (FR 4.3), the live
 * notification bell (FR 2.3) and the account menu. Every authenticated route
 * renders into the <Outlet/> below.
 */
export function Layout() {
  const { user, logout, isAdmin } = useAuth()
  const { campusId, setCampusId } = useCampusFilter()
  const queryClient = useQueryClient()
  const toast = useToast()
  const navigate = useNavigate()
  const location = useLocation()

  const [bellOpen, setBellOpen] = useState(false)
  const [menuOpen, setMenuOpen] = useState(false)
  const bellRef = useRef<HTMLDivElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)

  useDismissOnOutsideClick(bellRef, bellOpen, () => setBellOpen(false))
  useDismissOnOutsideClick(menuRef, menuOpen, () => setMenuOpen(false))

  // Close both popovers on navigation.
  useEffect(() => {
    setBellOpen(false)
    setMenuOpen(false)
  }, [location.pathname])

  const campusesQuery = useQuery({
    queryKey: ['campuses'],
    queryFn: () => api.get<Campus[]>('/campuses'),
    staleTime: 60 * 60 * 1000,
  })

  const notificationsQuery = useQuery({
    queryKey: notificationKeys.list({ page_size: 8 }),
    queryFn: () =>
      api.get<Page<Notification>>('/notifications', { query: { page: 1, page_size: 8 } }),
    enabled: Boolean(user),
    // The socket is the fast path; this poll is the safety net for a dropped one.
    refetchInterval: 2 * 60 * 1000,
  })

  // The list is a plain `Page` -- it carries no unread total, so the badge comes
  // from the dedicated counter route rather than from counting the first page.
  const unreadQuery = useQuery({
    queryKey: [...notificationKeys.all, 'unread-count'],
    queryFn: () => api.get<UnreadCountResponse>('/notifications/unread-count'),
    enabled: Boolean(user),
    refetchInterval: 2 * 60 * 1000,
  })

  useNotificationSocket({
    enabled: Boolean(user),
    onNotification: (item) => toast.info(notificationLabel(item.type), item.message),
  })

  const markRead = useMutation({
    mutationFn: (notificationId: string) =>
      api.post<void>(`/notifications/${notificationId}/read`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: notificationKeys.all }),
  })

  const items = notificationsQuery.data?.items ?? []
  const unreadCount = unreadQuery.data?.count ?? items.filter((item) => !item.is_read).length

  const openNotification = (item: Notification): void => {
    if (!item.is_read) markRead.mutate(item.notification_id)
    setBellOpen(false)
    navigate(notificationTarget(item))
  }

  const handleSignOut = async (): Promise<void> => {
    await logout()
    navigate('/login', { replace: true })
  }

  return (
    <div className="flex min-h-screen flex-col">
      <header className="sticky top-0 z-40 border-b border-slate-200 bg-white/95 backdrop-blur">
        <div className="mx-auto flex h-16 max-w-7xl items-center gap-3 px-4 sm:px-6 lg:px-8">
          <Link to="/" className="flex shrink-0 items-center gap-2">
            <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-brand-600 text-sm font-bold text-white">
              NV
            </span>
            <span className="hidden text-base font-semibold tracking-tight text-slate-900 sm:block">
              NoteVault
            </span>
          </Link>

          <nav className="ml-4 hidden items-center gap-1 md:flex">
            {NAV_ITEMS.map((item) => (
              <NavLink key={item.to} to={item.to} end={item.end} className={navLinkClasses}>
                {item.label}
              </NavLink>
            ))}
          </nav>

          <div className="ml-auto flex items-center gap-2">
            <Link to="/upload" className="btn btn-primary hidden px-3 py-1.5 text-xs sm:inline-flex">
              Upload
            </Link>

            {/* Campus scope (FR 4.3) */}
            <label className="hidden lg:block">
              <span className="sr-only">Campus</span>
              <select
                value={campusId ?? ''}
                onChange={(event) => setCampusId(event.target.value || null)}
                className="input h-9 w-44 py-0 text-xs"
              >
                <option value="">All campuses</option>
                {(campusesQuery.data ?? []).map((campus) => (
                  <option key={campus.campus_id} value={campus.campus_id}>
                    {campus.name}
                  </option>
                ))}
              </select>
            </label>

            {/* Notification bell (FR 2.3) */}
            <div ref={bellRef} className="relative">
              <button
                type="button"
                onClick={() => setBellOpen((open) => !open)}
                aria-haspopup="menu"
                aria-expanded={bellOpen}
                aria-label={
                  unreadCount > 0 ? `Notifications, ${unreadCount} unread` : 'Notifications'
                }
                className="relative rounded-lg p-2 text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-700"
              >
                <svg
                  aria-hidden="true"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth={1.7}
                  className="h-5 w-5"
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    d="M14.857 17.082a24 24 0 0 0 3.844-.494 8.97 8.97 0 0 1-1.494-4.994V9a5.207 5.207 0 0 0-10.414 0v2.594a8.97 8.97 0 0 1-1.494 4.994 24 24 0 0 0 3.844.494m5.714 0a3 3 0 1 1-5.714 0m5.714 0a24.3 24.3 0 0 1-5.714 0"
                  />
                </svg>
                {unreadCount > 0 ? (
                  <span className="absolute right-1 top-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-state-rejected px-1 text-[10px] font-semibold leading-none text-white">
                    {unreadCount > 9 ? '9+' : unreadCount}
                  </span>
                ) : null}
              </button>

              {bellOpen ? (
                <div className="absolute right-0 mt-2 w-80 animate-slide-up overflow-hidden rounded-xl bg-white shadow-pop ring-1 ring-slate-200">
                  <div className="flex items-center justify-between border-b border-slate-100 px-4 py-2.5">
                    <p className="text-sm font-semibold text-slate-900">Notifications</p>
                    <Link to="/notifications" className="text-xs font-medium text-brand-700">
                      See all
                    </Link>
                  </div>

                  {items.length === 0 ? (
                    <p className="px-4 py-8 text-center text-sm text-slate-500">
                      Nothing yet. Matches, messages and rental reminders land here.
                    </p>
                  ) : (
                    <ul className="max-h-96 divide-y divide-slate-100 overflow-y-auto">
                      {items.map((item) => (
                        <li key={item.notification_id}>
                          <button
                            type="button"
                            onClick={() => openNotification(item)}
                            className={cx(
                              'flex w-full gap-2.5 px-4 py-3 text-left transition-colors hover:bg-slate-50',
                              !item.is_read && 'bg-brand-50/50',
                            )}
                          >
                            <span
                              aria-hidden="true"
                              className={cx(
                                'mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full',
                                item.is_read ? 'bg-transparent' : 'bg-brand-600',
                              )}
                            />
                            <span className="min-w-0 flex-1">
                              <span className="block text-xs font-semibold text-slate-500">
                                {notificationLabel(item.type)}
                              </span>
                              <span className="mt-0.5 block text-sm leading-snug text-slate-800">
                                {item.message}
                              </span>
                              <span className="mt-1 block text-xs text-slate-400">
                                {relativeTime(item.created_at)}
                              </span>
                            </span>
                          </button>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              ) : null}
            </div>

            {/* Account menu */}
            <div ref={menuRef} className="relative">
              <button
                type="button"
                onClick={() => setMenuOpen((open) => !open)}
                aria-haspopup="menu"
                aria-expanded={menuOpen}
                className="flex items-center gap-2 rounded-lg p-1 pr-2 transition-colors hover:bg-slate-100"
              >
                <span className="flex h-8 w-8 items-center justify-center rounded-full bg-brand-100 text-xs font-semibold text-brand-800">
                  {initials(user?.name)}
                </span>
                <span className="hidden max-w-24 truncate text-sm font-medium text-slate-700 lg:block">
                  {user?.name ?? 'Account'}
                </span>
              </button>

              {menuOpen ? (
                <div className="absolute right-0 mt-2 w-56 animate-slide-up overflow-hidden rounded-xl bg-white py-1 shadow-pop ring-1 ring-slate-200">
                  <div className="border-b border-slate-100 px-4 py-2.5">
                    <p className="truncate text-sm font-semibold text-slate-900">{user?.name}</p>
                    <p className="truncate text-xs text-slate-500">{user?.email}</p>
                  </div>

                  {ACCOUNT_LINKS.map((link) => (
                    <Link
                      key={link.to}
                      to={link.to}
                      className="block px-4 py-2 text-sm text-slate-700 transition-colors hover:bg-slate-50"
                    >
                      {link.label}
                    </Link>
                  ))}

                  {isAdmin ? (
                    <Link
                      to="/admin"
                      className="block border-t border-slate-100 px-4 py-2 text-sm font-medium text-brand-700 transition-colors hover:bg-brand-50"
                    >
                      Admin panel
                    </Link>
                  ) : null}

                  <button
                    type="button"
                    onClick={() => void handleSignOut()}
                    className="block w-full border-t border-slate-100 px-4 py-2 text-left text-sm text-slate-700 transition-colors hover:bg-slate-50"
                  >
                    Sign out
                  </button>
                </div>
              ) : null}
            </div>
          </div>
        </div>

        {/* Primary nav collapses to a scrollable strip on small screens. */}
        <nav className="flex items-center gap-1 overflow-x-auto border-t border-slate-100 px-4 py-2 md:hidden">
          {NAV_ITEMS.map((item) => (
            <NavLink key={item.to} to={item.to} end={item.end} className={navLinkClasses}>
              {item.label}
            </NavLink>
          ))}
          <NavLink to="/upload" className={navLinkClasses}>
            Upload
          </NavLink>
        </nav>
      </header>

      {/* The shell stays put while a code-split page loads. */}
      <main className="mx-auto w-full max-w-7xl flex-1 px-4 py-6 sm:px-6 lg:px-8 lg:py-8">
        <Suspense fallback={<PageSpinner />}>
          <Outlet />
        </Suspense>
      </main>

      <footer className="border-t border-slate-200 bg-white">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-2 px-4 py-4 text-xs text-slate-400 sm:px-6 lg:px-8">
          <p>NoteVault — campus study material exchange</p>
          <p>CSE470 Software Engineering</p>
        </div>
      </footer>
    </div>
  )
}

export default Layout
