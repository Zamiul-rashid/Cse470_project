import { useState, type FormEvent } from 'react'
import { Link, Navigate, useNavigate, useSearchParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'

import { Button } from '../../components/Button'
import { ErrorBox } from '../../components/ErrorBox'
import { Input } from '../../components/Input'
import { Select } from '../../components/Select'
import { PageSpinner } from '../../components/Spinner'
import { api } from '../../lib/api'
import { useAuth } from '../../lib/auth'
import type { Campus } from '../../lib/types'

/** Mirrors the server-side rule; keep the two in step. */
const MIN_PASSWORD_LENGTH = 8

type FieldName = 'name' | 'email' | 'password' | 'confirm' | 'campus_id'
type FieldErrors = Partial<Record<FieldName, string>>

function safeRedirect(target: string | null): string {
  if (!target) return '/'
  if (!target.startsWith('/') || target.startsWith('//')) return '/'
  if (target.startsWith('/login') || target.startsWith('/register')) return '/'
  return target
}

export default function RegisterPage() {
  const { user, loading, register } = useAuth()
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()

  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [campusId, setCampusId] = useState('')
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({})
  const [error, setError] = useState<unknown>(null)
  const [submitting, setSubmitting] = useState(false)

  // FR 4.3 -- the campus list is the same one that scopes search and the
  // leaderboard, so it is cached for the session rather than per screen.
  const campusesQuery = useQuery({
    queryKey: ['campuses'],
    queryFn: () => api.get<Campus[]>('/campuses'),
    staleTime: 60 * 60 * 1000,
  })

  const redirectTo = safeRedirect(searchParams.get('next'))

  const validate = (): FieldErrors => {
    const errors: FieldErrors = {}
    if (name.trim().length < 2) errors.name = 'Tell us what to call you.'
    if (!/^\S+@\S+\.\S+$/.test(email.trim())) errors.email = 'That does not look like an email address.'
    if (password.length < MIN_PASSWORD_LENGTH) {
      errors.password = `Use at least ${MIN_PASSWORD_LENGTH} characters.`
    }
    if (confirm !== password) errors.confirm = 'The two passwords do not match.'
    if (!campusId) errors.campus_id = 'Pick the campus you study at.'
    return errors
  }

  const handleSubmit = async (event: FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault()

    const errors = validate()
    setFieldErrors(errors)
    if (Object.keys(errors).length > 0) return

    setSubmitting(true)
    setError(null)
    try {
      // `register` signs the new account straight in -- either from the token
      // pair the endpoint returns, or by logging in with the same credentials.
      await register({ name: name.trim(), email: email.trim(), password, campus_id: campusId })
      navigate(redirectTo, { replace: true })
    } catch (cause) {
      setError(cause)
    } finally {
      setSubmitting(false)
    }
  }

  if (loading) return <PageSpinner label="Checking your session" />
  if (user) return <Navigate to={redirectTo} replace />

  const campuses = campusesQuery.data ?? []

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
          <h1 className="text-xl font-semibold tracking-tight text-slate-900">Create your account</h1>
          <p className="mt-1.5 text-sm leading-relaxed text-slate-500">
            One account lets you list, borrow, buy and swap study material across your campus.
          </p>

          {error ? (
            <ErrorBox error={error} title="Could not create your account" className="mt-5" />
          ) : null}

          <form className="mt-6 space-y-4" onSubmit={(event) => void handleSubmit(event)} noValidate>
            <Input
              label="Full name"
              name="name"
              autoComplete="name"
              placeholder="Ayesha Rahman"
              value={name}
              required
              error={fieldErrors.name}
              onChange={(event) => setName(event.target.value)}
            />

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
              autoComplete="new-password"
              value={password}
              required
              error={fieldErrors.password}
              hint={`At least ${MIN_PASSWORD_LENGTH} characters.`}
              onChange={(event) => setPassword(event.target.value)}
            />

            <Input
              label="Confirm password"
              type="password"
              name="confirm-password"
              autoComplete="new-password"
              value={confirm}
              required
              error={fieldErrors.confirm}
              onChange={(event) => setConfirm(event.target.value)}
            />

            <Select
              label="Campus"
              name="campus_id"
              value={campusId}
              required
              placeholder={campusesQuery.isPending ? 'Loading campuses…' : 'Select your campus'}
              disabled={campusesQuery.isPending || campusesQuery.isError}
              error={fieldErrors.campus_id}
              hint="This scopes what you see first. You can browse other campuses later."
              onChange={(event) => setCampusId(event.target.value)}
              options={campuses.map((campus) => ({
                value: campus.campus_id,
                label: `${campus.name} — ${campus.location}`,
              }))}
            />

            {campusesQuery.isError ? (
              <ErrorBox
                error={campusesQuery.error}
                title="Could not load the campus list"
                onRetry={() => void campusesQuery.refetch()}
              />
            ) : null}

            <Button type="submit" fullWidth loading={submitting} className="mt-2">
              Create account
            </Button>
          </form>
        </div>

        <p className="mt-6 text-center text-sm text-slate-500">
          Already registered?{' '}
          <Link to="/login" className="font-medium text-brand-700 hover:text-brand-800">
            Sign in
          </Link>
        </p>
      </div>
    </div>
  )
}
