import { Suspense, lazy, type ReactElement } from 'react'
import { Navigate, Outlet, Route, Routes, useLocation } from 'react-router-dom'

import { Layout } from './components/Layout'
import { PageSpinner } from './components/Spinner'
import { useAuth } from './lib/auth'

/*
 * Every feature page is code-split, so the login screen does not ship the admin
 * panel. Each of these modules must have a **default export** -- that is the
 * contract React.lazy relies on.
 */
const LoginPage = lazy(() => import('./features/auth/LoginPage'))
const RegisterPage = lazy(() => import('./features/auth/RegisterPage'))
const ProfilePage = lazy(() => import('./features/auth/ProfilePage'))
const PublicProfilePage = lazy(() => import('./features/auth/PublicProfilePage'))

const BrowsePage = lazy(() => import('./features/listings/BrowsePage'))
const ListingDetailPage = lazy(() => import('./features/listings/ListingDetailPage'))
const UploadPage = lazy(() => import('./features/listings/UploadPage'))
const MyListingsPage = lazy(() => import('./features/listings/MyListingsPage'))

const WishlistPage = lazy(() => import('./features/community/WishlistPage'))
const RequestsPage = lazy(() => import('./features/community/RequestsPage'))
const MessagesPage = lazy(() => import('./features/community/MessagesPage'))
const NotificationsPage = lazy(() => import('./features/community/NotificationsPage'))

const TransactionsPage = lazy(() => import('./features/transactions/TransactionsPage'))
const TransactionDetailPage = lazy(() => import('./features/transactions/TransactionDetailPage'))
const RentalsPage = lazy(() => import('./features/transactions/RentalsPage'))

const LeaderboardPage = lazy(() => import('./features/analytics/LeaderboardPage'))
const TrendingPage = lazy(() => import('./features/analytics/TrendingPage'))

const AdminHomePage = lazy(() => import('./features/admin/AdminHomePage'))
const ModerationPage = lazy(() => import('./features/admin/ModerationPage'))
const ReportsPage = lazy(() => import('./features/admin/ReportsPage'))

const NotFoundPage = lazy(() => import('./components/NotFound'))

/**
 * Gate for everything behind a session. Waits for the initial `/users/me`
 * check rather than bouncing a signed-in user on a hard refresh, and carries
 * the attempted location so login can return there.
 */
function RequireAuth({ children }: { children: ReactElement }): ReactElement {
  const { user, loading } = useAuth()
  const location = useLocation()

  if (loading) return <PageSpinner label="Checking your session" />
  if (!user) return <Navigate to="/login" replace state={{ from: location }} />
  return children
}

/**
 * `users.role === 'ADMIN'`. This only hides the UI --
 * the `require_admin` dependency on the server is the actual guard.
 */
function RequireAdmin(): ReactElement {
  const { user, loading, isAdmin } = useAuth()

  if (loading) return <PageSpinner label="Checking your session" />
  if (!user) return <Navigate to="/login" replace />
  if (!isAdmin) return <Navigate to="/" replace />
  return <Outlet />
}

export default function App() {
  return (
    <Suspense fallback={<PageSpinner />}>
      <Routes>
        {/* Unauthenticated */}
        <Route path="/login" element={<LoginPage />} />
        <Route path="/register" element={<RegisterPage />} />

        {/* Everything else renders inside the app shell */}
        <Route
          element={
            <RequireAuth>
              <Layout />
            </RequireAuth>
          }
        >
          {/* Module 1 -- listings & discovery */}
          <Route index element={<BrowsePage />} />
          <Route path="materials/:id" element={<ListingDetailPage />} />
          <Route path="upload" element={<UploadPage />} />
          <Route path="my-listings" element={<MyListingsPage />} />

          {/* Module 2 -- community */}
          <Route path="wishlist" element={<WishlistPage />} />
          <Route path="requests" element={<RequestsPage />} />
          <Route path="messages" element={<MessagesPage />} />
          <Route path="messages/:id" element={<MessagesPage />} />
          <Route path="notifications" element={<NotificationsPage />} />

          {/* Module 3 -- transactions & trust */}
          <Route path="transactions" element={<TransactionsPage />} />
          <Route path="transactions/:id" element={<TransactionDetailPage />} />
          <Route path="rentals" element={<RentalsPage />} />

          {/* Profile */}
          <Route path="profile" element={<ProfilePage />} />
          <Route path="users/:id" element={<PublicProfilePage />} />

          {/* Module 4 -- analytics */}
          <Route path="leaderboard" element={<LeaderboardPage />} />
          <Route path="trending" element={<TrendingPage />} />

          {/* Module 4 -- admin */}
          <Route element={<RequireAdmin />}>
            <Route path="admin" element={<AdminHomePage />} />
            <Route path="admin/moderation" element={<ModerationPage />} />
            <Route path="admin/reports" element={<ReportsPage />} />
          </Route>

          <Route path="*" element={<NotFoundPage />} />
        </Route>
      </Routes>
    </Suspense>
  )
}
