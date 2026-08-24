import type { ReactNode } from 'react'

import { ApiError } from '../lib/api'
import { cx } from '../lib/format'
import { Button } from './Button'

export interface ErrorBoxProps {
  /** Anything thrown by the API client, a query, or a mutation. */
  error: unknown
  title?: string
  /** Wire to a query's `refetch` to offer a retry. */
  onRetry?: () => void
  className?: string
  children?: ReactNode
}

/** Best-effort readable message out of an unknown thrown value. */
export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.detail
  if (error instanceof Error) return error.message
  if (typeof error === 'string') return error
  return 'Something went wrong.'
}

function defaultTitle(error: unknown): string {
  if (!(error instanceof ApiError)) return 'Something went wrong'
  if (error.isNetworkError) return 'Cannot reach the server'
  switch (error.status) {
    case 403:
      return 'You do not have access to this'
    case 404:
      return 'Not found'
    case 409:
      return 'That is not possible right now'
    case 422:
      return 'Check the highlighted fields'
    default:
      return error.status >= 500 ? 'The server had a problem' : 'Something went wrong'
  }
}

/** Inline error panel. Use for failed loads; use a toast for failed actions. */
export function ErrorBox({ error, title, onRetry, className, children }: ErrorBoxProps) {
  if (!error) return null

  return (
    <div
      role="alert"
      className={cx('rounded-xl bg-red-50 p-4 ring-1 ring-inset ring-red-200', className)}
    >
      <div className="flex gap-3">
        <svg
          aria-hidden="true"
          viewBox="0 0 20 20"
          fill="currentColor"
          className="h-5 w-5 shrink-0 text-red-600"
        >
          <path
            fillRule="evenodd"
            d="M10 1.75a8.25 8.25 0 1 0 0 16.5 8.25 8.25 0 0 0 0-16.5ZM10 5a.75.75 0 0 1 .75.75v4.5a.75.75 0 0 1-1.5 0v-4.5A.75.75 0 0 1 10 5Zm0 9.5a1 1 0 1 0 0-2 1 1 0 0 0 0 2Z"
            clipRule="evenodd"
          />
        </svg>
        <div className="min-w-0 flex-1">
          <h3 className="text-sm font-semibold text-red-900">{title ?? defaultTitle(error)}</h3>
          <p className="mt-1 break-words text-sm text-red-800">{errorMessage(error)}</p>
          {children ? <div className="mt-2 text-sm text-red-800">{children}</div> : null}
          {onRetry ? (
            <Button variant="ghost" size="sm" className="mt-3" onClick={onRetry}>
              Try again
            </Button>
          ) : null}
        </div>
      </div>
    </div>
  )
}

export default ErrorBox
