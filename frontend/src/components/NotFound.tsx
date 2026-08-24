import { Link, useLocation } from 'react-router-dom'

/** Terminal `*` route. Renders inside the app shell when the user is signed in. */
export function NotFound() {
  const { pathname } = useLocation()

  return (
    <div className="mx-auto flex max-w-lg flex-col items-center px-4 py-20 text-center">
      <p className="text-5xl font-semibold tracking-tight text-brand-600">404</p>
      <h1 className="mt-4 text-xl font-semibold text-slate-900">This page does not exist</h1>
      <p className="mt-2 text-sm leading-relaxed text-slate-500">
        Nothing is served at{' '}
        <code className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-xs text-slate-700">
          {pathname}
        </code>
        . It may have been removed, or the link may be out of date.
      </p>
      <div className="mt-6 flex gap-2">
        <Link to="/" className="btn btn-primary">
          Browse materials
        </Link>
        <Link to="/my-listings" className="btn btn-ghost">
          My listings
        </Link>
      </div>
    </div>
  )
}

export default NotFound
