import type { ReactNode } from 'react'

import { cx, listingTypeColor, listingTypeLabel } from '../lib/format'
import type { ListingType } from '../lib/types'

export interface PillProps {
  children: ReactNode
  /** Token colour classes, e.g. from `listingTypeColor()`. Defaults to neutral. */
  color?: string
  /** Solid dot before the label -- useful when several pills sit in a row. */
  dot?: string
  className?: string
  title?: string
}

/** Small labelled chip. Geometry comes from the `.pill` class in index.css. */
export function Pill({ children, color, dot, className, title }: PillProps) {
  return (
    <span
      title={title}
      className={cx('pill', color ?? 'bg-slate-100 text-slate-600 ring-slate-200', className)}
    >
      {dot ? <span aria-hidden="true" className={cx('h-1.5 w-1.5 rounded-full', dot)} /> : null}
      {children}
    </span>
  )
}

/** FR 1.3 -- the listing-type tag, coloured from the design tokens. */
export function ListingTypePill({ type, className }: { type: ListingType; className?: string }) {
  return (
    <Pill color={listingTypeColor(type)} className={className}>
      {listingTypeLabel(type)}
    </Pill>
  )
}

export default Pill
