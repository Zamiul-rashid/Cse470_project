import type { ReactNode } from 'react'

import { cx } from '../lib/format'

export interface EmptyStateProps {
  title: string
  /** One sentence saying what to do next -- not just "no data". */
  description?: ReactNode
  /** Inline SVG or emoji. A default document glyph is used when omitted. */
  icon?: ReactNode
  /** A button or link. */
  action?: ReactNode
  className?: string
  /** `inline` drops the card chrome, for use inside an existing panel. */
  variant?: 'card' | 'inline'
}

const DefaultIcon = (
  <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" strokeWidth={1.5} stroke="currentColor">
    <path
      strokeLinecap="round"
      strokeLinejoin="round"
      d="M19.5 14.25v-2.625a3.375 3.375 0 0 0-3.375-3.375h-1.5A1.125 1.125 0 0 1 13.5 7.125v-1.5a3.375 3.375 0 0 0-3.375-3.375H8.25M9 16.5v.75m3-3v3M15 12v5.25m-4.5-15H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 0 0-9-9Z"
    />
  </svg>
)

/**
 * The screen a grader sees most often on a fresh clone. Say what would fill
 * this space and how to get there -- never just "nothing here".
 */
export function EmptyState({
  title,
  description,
  icon,
  action,
  className,
  variant = 'card',
}: EmptyStateProps) {
  return (
    <div
      className={cx(
        'flex flex-col items-center justify-center px-6 py-12 text-center',
        variant === 'card' && 'card',
        className,
      )}
    >
      <div className="mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-brand-50 text-brand-600">
        <span className="h-6 w-6">{icon ?? DefaultIcon}</span>
      </div>
      <h3 className="text-sm font-semibold text-slate-900">{title}</h3>
      {description ? (
        <p className="mt-1.5 max-w-sm text-sm leading-relaxed text-slate-500">{description}</p>
      ) : null}
      {action ? <div className="mt-5">{action}</div> : null}
    </div>
  )
}

export default EmptyState
