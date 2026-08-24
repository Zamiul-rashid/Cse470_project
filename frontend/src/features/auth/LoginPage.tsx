import { useState, type FormEvent } from 'react'
import { Link, Navigate, useLocation, useNavigate, useSearchParams } from 'react-router-dom'

import { Button } from '../../components/Button'
import { ErrorBox } from '../../components/ErrorBox'
import { Input } from '../../components/Input'
import { PageSpinner } from '../../components/Spinner'
import { useAuth } from '../../lib/auth'

/**
 * Where to land after a successful sign-in.
 *
 * Two callers put a destination in front of us: `<RequireAuth>` in App.tsx
 * stashes the attempted location in `location.state.from`, and the API client's
 * `endSession()` bounces an expired session to `/login?next=...`. Both are
 * honoured, and both are sanitised -- a redirect target is user-controllable
 * input, so anything that is not a plain in-app path is thrown away.
 */
interface FromLocationState {
  from?: { pathname?: string; search?: string; hash?: string }
}

function safeRedirect(target: string | null | undefined): string {
  if (!target) return '/'
  // `//evil.example` is a protocol-relative absolute URL, not an in-app path.
  if (!target.startsWith('/') || target.startsWith('//')) return '/'
  if (target.startsWith('/login') || target.startsWith('/register')) return '/'
  return target
}

export default function LoginPage() {
  const { user, loading, login } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [searchParams] = useSearchParams()

  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [fieldErrors, setFieldErrors] = useState<{ email?: string; password?: string }>({})
  const [error, setError] = useState<unknown>(null)
  const [submitting, setSubmitting] = useState(false)

  const state = location.state as FromLocationState | null
  const fromState = state?.from?.pathname
    ? `${state.from.pathname}${state.from.search ?? ''}${state.from.hash ?? ''}`
    : null
  const redirectTo = safeRedirect(fromState ?? searchParams.get('next'))

  const handleSubmit = async (event: FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault()

    const errors: { email?: string; password?: string } = {}
    if (!email.trim()) errors.email = 'Enter your campus email.'
    if (!password) errors.password = 'Enter your password.'
    setFieldErrors(errors)
    if (Object.keys(errors).length > 0) return

    setSubmitting(true)
    setError(null)
    try {
      await login(email.trim(), password)
      navigate(redirectTo, { replace: true })
    } catch (cause) {
      setError(cause)
      setPassword('')
    } finally {
      setSubmitting(false)
    }
  }

  if (loading) return <PageSpinner label="Checking your session" />
  if (user) return <Navigate to={redirectTo} replace />

  return (
    <div className="flex min-h-screen flex-col justify-center bg-slate-50 px-4 py-12">
      <div className="mx-auto w-full max-w-md">
        <Link to="/" className="mb-8 flex items-center justify-center gap-2">
          <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-brand-600 text-sm font-bold text-white">
            NV
          </span>
          <span className="text-lg font-semibold tracking-tight text-slate-900">NoteVault</span>
        </Link>

        <div className="card p-6 sm:p-8">
          <h1 className="text-xl font-semibold tracking-tight text-slate-900">Sign in</h1>
          <p className="mt-1.5 text-sm leading-relaxed text-slate-500">
            Use the email you registered with your campus.
          </p>

          {redirectTo !== '/' ? (
            <p className="mt-4 rounded-lg bg-brand-50 px-3 py-2 text-xs leading-relaxed text-brand-800">
              Sign in to continue to{' '}
              <span className="font-medium">{redirectTo}</span>.
            </p>
          ) : null}

          {error ? <ErrorBox error={error} title="Could not sign you in" className="mt-5" /> : null}

          <form className="mt-6 space-y-4" onSubmit={(event) => void handleSubmit(event)} noValidate>
            <Input
              label="Email"
              type="email"
              name="email"
              autoComplete="email"
              placeholder="you@campus.edu"
              value={email}
              required
              error={fieldErrors.email}
              onChange={(event) => setEmail(event.target.value)}
            />

            <Input
              label="Password"
              type="password"
              name="password"
              autoComplete="current-password"
              placeholder="••••••••"
              value={password}
              required
              error={fieldErrors.password}
              onChange={(event) => setPassword(event.target.value)}
            />

            <Button type="submit" fullWidth loading={submitting} className="mt-2">
              Sign in
            </Button>
          </form>
        </div>

        <p className="mt-6 text-center text-sm text-slate-500">
          New here?{' '}
          <Link
            to={`/register${searchParams.get('next') ? `?next=${encodeURIComponent(searchParams.get('next') ?? '')}` : ''}`}
            className="font-medium text-brand-700 hover:text-brand-800"
          >
            Create an account
          </Link>
        </p>
      </div>
    </div>
  )
}
