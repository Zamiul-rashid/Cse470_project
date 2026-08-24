import { useState } from 'react'

import { cx, pluralize } from '../lib/format'

export type StarSize = 'sm' | 'md' | 'lg'

export interface StarRatingProps {
  /** 0-5. Fractional values render a partly-filled star in display mode. */
  value: number
  /** Providing this switches the component into input mode (FR 2.5). */
  onChange?: (value: number) => void
  size?: StarSize
  /** Appends "(12 reviews)". Pass null to hide. */
  count?: number | null
  /** Appends the numeric value, e.g. "4.8". */
  showValue?: boolean
  className?: string
  /** Accessible name for the input group. */
  label?: string
  disabled?: boolean
}

const SIZES: Record<StarSize, string> = {
  sm: 'h-3.5 w-3.5',
  md: 'h-5 w-5',
  lg: 'h-7 w-7',
}

const STAR_PATH =
  'M10.79 2.29a.9.9 0 0 1 1.62 0l2.09 4.24 4.68.68a.9.9 0 0 1 .5 1.54l-3.39 3.3.8 4.66a.9.9 0 0 1-1.31.95L11.6 15.46l-4.18 2.2a.9.9 0 0 1-1.31-.95l.8-4.66-3.39-3.3a.9.9 0 0 1 .5-1.54l4.68-.68 2.09-4.24Z'

function Star({ className }: { className: string }) {
  return (
    <svg aria-hidden="true" viewBox="0 0 22 20" fill="currentColor" className={className}>
      <path d={STAR_PATH} />
    </svg>
  )
}

/**
 * Read-only by default; pass `onChange` for the review form.
 *
 * The fill overlay is width-driven, which is one of the few places an inline
 * style is warranted -- the percentage is genuinely computed.
 */
export function StarRating({
  value,
  onChange,
  size = 'md',
  count,
  showValue = false,
  className,
  label = 'Rating',
  disabled = false,
}: StarRatingProps) {
  const [hovered, setHovered] = useState<number | null>(null)
  const interactive = Boolean(onChange) && !disabled
  const starClass = SIZES[size]

  if (interactive) {
    const shown = hovered ?? value
    return (
      <div className={cx('flex items-center gap-2', className)}>
        <div
          role="radiogroup"
          aria-label={label}
          className="flex items-center gap-0.5"
          onMouseLeave={() => setHovered(null)}
        >
          {[1, 2, 3, 4, 5].map((star) => (
            <button
              key={star}
              type="button"
              role="radio"
              aria-checked={value === star}
              aria-label={pluralize(star, 'star')}
              disabled={disabled}
              onMouseEnter={() => setHovered(star)}
              onFocus={() => setHovered(star)}
              onBlur={() => setHovered(null)}
              onClick={() => onChange?.(star)}
              className={cx(
                'rounded p-0.5 transition-colors',
                star <= shown ? 'text-amber-400' : 'text-slate-300 hover:text-amber-200',
              )}
            >
              <Star className={starClass} />
            </button>
          ))}
        </div>
        {showValue ? (
          <span className="text-sm font-medium tabular-nums text-slate-700">{shown || '–'}</span>
        ) : null}
      </div>
    )
  }

  const clamped = Math.max(0, Math.min(5, value || 0))
  const percent = (clamped / 5) * 100

  return (
    <span
      className={cx('inline-flex items-center gap-1.5', className)}
      title={`${clamped.toFixed(1)} out of 5`}
    >
      <span className="relative inline-flex" role="img" aria-label={`${clamped.toFixed(1)} out of 5`}>
        <span className="flex gap-0.5 text-slate-200">
          {[0, 1, 2, 3, 4].map((i) => (
            <Star key={i} className={starClass} />
          ))}
        </span>
        <span
          aria-hidden="true"
          className="absolute inset-y-0 left-0 overflow-hidden"
          style={{ width: `${percent}%` }}
        >
          <span className="flex gap-0.5 text-amber-400">
            {[0, 1, 2, 3, 4].map((i) => (
              <Star key={i} className={starClass} />
            ))}
          </span>
        </span>
      </span>

      {showValue ? (
        <span className="text-sm font-medium tabular-nums text-slate-700">
          {clamped.toFixed(1)}
        </span>
      ) : null}
      {count !== null && count !== undefined ? (
        <span className="text-xs text-slate-500">({count})</span>
      ) : null}
    </span>
  )
}

export default StarRating
