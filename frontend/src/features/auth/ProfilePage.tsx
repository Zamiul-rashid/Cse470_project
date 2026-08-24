import { useEffect, useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { Button } from '../../components/Button'
import { ErrorBox } from '../../components/ErrorBox'
import { Input } from '../../components/Input'
import { Select } from '../../components/Select'
import { PageSpinner } from '../../components/Spinner'
import { StarRating } from '../../components/StarRating'
import { useToast } from '../../components/Toast'
import { api } from '../../lib/api'
import { useAuth } from '../../lib/auth'
import { formatDate, formatRating, initials } from '../../lib/format'
import type { Campus, ExportFormat, User, UserUpdate } from '../../lib/types'

/**
 * FR 4.5 -- the export is an authenticated GET, so it cannot be a plain
 * `<a href>`: the browser would send it without the bearer token. Pull it
 * through the API client as a blob instead and hand the object URL to a
 * synthetic anchor.
 *
 * The route is `/me/export`, never `/users/{id}/export` -- the user id comes
 * off the token, so there is no parameter to tamper
 * with.
 */
const EXPORT_FORMATS: { format: ExportFormat; label: string; description: string }[] = [
  { format: 'csv', label: 'Download CSV', description: 'Spreadsheet-ready rows.' },
  { format: 'pdf', label: 'Download PDF', description: 'Formatted report to print or attach.' },
]

function StatTile({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-lg bg-slate-50 px-4 py-3 ring-1 ring-inset ring-slate-200">
      <p className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</p>
      <p className="mt-1 text-xl font-semibold tabular-nums text-slate-900">{value}</p>
      {hint ? <p className="mt-0.5 text-xs text-slate-400">{hint}</p> : null}
    </div>
  )
}

export default function ProfilePage() {
  const { user, refreshUser } = useAuth()
  const queryClient = useQueryClient()
  const toast = useToast()

  const [name, setName] = useState('')
  const [campusId, setCampusId] = useState('')
  const [nameError, setNameError] = useState<string | null>(null)
  const [exporting, setExporting] = useState<ExportFormat | null>(null)

  // Seed the form from the session once it resolves, and again after a save.
  useEffect(() => {
    if (!user) return
    setName(user.name)
    setCampusId(user.campus_id)
  }, [user])

  const campusesQuery = useQuery({
    queryKey: ['campuses'],
    queryFn: () => api.get<Campus[]>('/campuses'),
    staleTime: 60 * 60 * 1000,
  })

  // `UserMe` is `UserPublic` plus email and created_at, so the session already
  // carries the rating and the upload counter -- there is nothing extra to fetch.
  const saveProfile = useMutation({
    mutationFn: (payload: UserUpdate) => api.patch<User>('/users/me', payload),
    onSuccess: async () => {
      await refreshUser()
      await queryClient.invalidateQueries({ queryKey: ['users', 'profile'] })
      toast.success('Profile updated')
    },
    onError: (cause) => toast.fromError(cause, 'Could not save your profile'),
  })

  const handleSave = (event: FormEvent<HTMLFormElement>): void => {
    event.preventDefault()
    if (name.trim().length < 2) {
      setNameError('Tell us what to call you.')
      return
    }
    setNameError(null)
    saveProfile.mutate({ name: name.trim(), campus_id: campusId })
  }

  const handleExport = async (format: ExportFormat): Promise<void> => {
    setExporting(format)
    try {
      const { blob, filename } = await api.blob('/me/export', { query: { format } })
      const objectUrl = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = objectUrl
      anchor.download =
        filename ?? `notevault-history-${new Date().toISOString().slice(0, 10)}.${format}`
      document.body.appendChild(anchor)
      anchor.click()
      anchor.remove()
      // The download is handed to the browser synchronously, but revoking on a
      // short delay is the safe form across engines.
      window.setTimeout(() => URL.revokeObjectURL(objectUrl), 30_000)
      toast.success('Export ready', `Your history was downloaded as ${format.toUpperCase()}.`)
    } catch (cause) {
      toast.fromError(cause, 'Export failed')
    } finally {
      setExporting(null)
    }
  }

  if (!user) return <PageSpinner label="Loading your profile" />

  const dirty = name.trim() !== user.name || campusId !== user.campus_id

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-center gap-4">
        <span className="flex h-14 w-14 shrink-0 items-center justify-center rounded-full bg-brand-100 text-lg font-semibold text-brand-800">
          {initials(user.name)}
        </span>
        <div className="min-w-0">
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900">{user.name}</h1>
          <p className="mt-0.5 truncate text-sm text-slate-500">
            {user.email}
            {user.campus_name ? ` · ${user.campus_name}` : ''}
          </p>
        </div>
        <Link to={`/users/${user.user_id}`} className="btn btn-ghost ml-auto">
          View public profile
        </Link>
      </header>

      <div className="grid gap-6 lg:grid-cols-3">
        {/* ---------------------------------------------------- edit form */}
        <section className="card p-6 lg:col-span-2">
          <h2 className="text-base font-semibold text-slate-900">Account details</h2>
          <p className="mt-1 text-sm text-slate-500">
            Your name is shown on every listing you post and on your reviews.
          </p>

          <form className="mt-5 space-y-4" onSubmit={handleSave} noValidate>
            <Input
              label="Full name"
              value={name}
              required
              error={nameError}
              onChange={(event) => setName(event.target.value)}
            />

            <Select
              label="Campus"
              value={campusId}
              required
              disabled={campusesQuery.isPending || campusesQuery.isError}
              hint="Moving campus changes which listings are shown to you first."
              onChange={(event) => setCampusId(event.target.value)}
              options={(campusesQuery.data ?? []).map((campus) => ({
                value: campus.campus_id,
                label: `${campus.name} — ${campus.location}`,
              }))}
            />

            <Input label="Email" value={user.email} disabled hint="Your campus email cannot be changed here." />

            {campusesQuery.isError ? (
              <ErrorBox
                error={campusesQuery.error}
                title="Could not load the campus list"
                onRetry={() => void campusesQuery.refetch()}
              />
            ) : null}

            <div className="flex items-center gap-3 pt-1">
              <Button type="submit" loading={saveProfile.isPending} disabled={!dirty}>
                Save changes
              </Button>
              {dirty ? (
                <button
                  type="button"
                  className="text-sm font-medium text-slate-500 hover:text-slate-700"
                  onClick={() => {
                    setName(user.name)
                    setCampusId(user.campus_id)
                    setNameError(null)
                  }}
                >
                  Discard
                </button>
              ) : (
                <p className="text-sm text-slate-400">Nothing to save.</p>
              )}
            </div>
          </form>
        </section>

        {/* ------------------------------------------------ rating summary */}
        <aside className="space-y-6">
          <section className="card p-6">
            <h2 className="text-base font-semibold text-slate-900">Your rating</h2>
            <div className="mt-4 flex items-baseline gap-3">
              <p className="text-3xl font-semibold tabular-nums text-slate-900">
                {formatRating(user.rating_avg, user.rating_count)}
              </p>
              <StarRating value={user.rating_avg} size="sm" count={null} />
            </div>
            <p className="mt-2 text-sm text-slate-500">
              {user.rating_count === 0
                ? 'No reviews yet. Ratings appear once a transaction you were part of is completed.'
                : `From ${user.rating_count} ${user.rating_count === 1 ? 'review' : 'reviews'}.`}
            </p>

            <div className="mt-5 grid grid-cols-2 gap-3">
              <StatTile label="Uploads" value={String(user.upload_count)} hint="Listings posted" />
              <StatTile
                label="Reviews"
                value={String(user.rating_count)}
                hint="Ratings received"
              />
            </div>
            <p className="mt-4 text-xs text-slate-400">Member since {formatDate(user.created_at)}</p>
          </section>

          {/* ------------------------------------------------ FR 4.5 export */}
          <section className="card p-6">
            <h2 className="text-base font-semibold text-slate-900">Export your history</h2>
            <p className="mt-1 text-sm leading-relaxed text-slate-500">
              Everything you have listed and every transaction you were part of, in one file.
              Only your own records are included.
            </p>

            <div className="mt-4 space-y-2">
              {EXPORT_FORMATS.map((item) => (
                <div key={item.format} className="flex items-center gap-3">
                  <Button
                    variant="ghost"
                    className="w-40 justify-start"
                    loading={exporting === item.format}
                    disabled={exporting !== null && exporting !== item.format}
                    onClick={() => void handleExport(item.format)}
                  >
                    {item.label}
                  </Button>
                  <p className="text-xs text-slate-500">{item.description}</p>
                </div>
              ))}
            </div>
          </section>
        </aside>
      </div>
    </div>
  )
}
