import { cx } from '../lib/format'

export interface SpinnerProps {
  size?: 'xs' | 'sm' | 'md' | 'lg'
  className?: string
  /** Screen-reader text. Set to null inside an element that already has one. */
  label?: string | null
}

const SIZES: Record<NonNullable<SpinnerProps['size']>, string> = {
  xs: 'h-3 w-3 border-[1.5px]',
  sm: 'h-4 w-4 border-2',
  md: 'h-6 w-6 border-2',
  lg: 'h-9 w-9 border-[3px]',
}

/** Indeterminate activity indicator. Inherits `currentColor`. */
export function Spinner({ size = 'sm', className, label = 'Loading' }: SpinnerProps) {
  return (
    <span
      role="status"
      className={cx(
        'inline-block shrink-0 animate-spin rounded-full border-current border-r-transparent align-[-0.125em]',
        SIZES[size],
        className,
      )}
    >
      {label ? <span className="sr-only">{label}</span> : null}
    </span>
  )
}

/** Centred spinner for a whole page or panel that has nothing to show yet. */
export function PageSpinner({ label = 'Loading' }: { label?: string }) {
  return (
    <div className="flex min-h-[40vh] w-full flex-col items-center justify-center gap-3 text-slate-400">
      <Spinner size="lg" label={null} />
      <p className="text-sm text-slate-500">{label}</p>
    </div>
  )
}

export default Spinner
