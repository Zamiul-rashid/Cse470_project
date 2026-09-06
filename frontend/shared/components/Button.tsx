import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from 'react'

import { cx } from '../lib/format'
import { Spinner } from './Spinner'

export type ButtonVariant = 'primary' | 'ghost' | 'danger' | 'subtle'
export type ButtonSize = 'sm' | 'md' | 'lg'

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  size?: ButtonSize
  /** Shows a spinner and disables the button. */
  loading?: boolean
  fullWidth?: boolean
  /** Rendered before the label -- a small inline SVG, typically. */
  icon?: ReactNode
}

const VARIANTS: Record<ButtonVariant, string> = {
  primary: 'btn-primary',
  ghost: 'btn-ghost',
  danger: 'btn-danger',
  subtle: 'bg-slate-100 text-slate-700 hover:bg-slate-200 active:bg-slate-300',
}

const SIZES: Record<ButtonSize, string> = {
  sm: 'px-2.5 py-1.5 text-xs',
  md: '', // the .btn default
  lg: 'px-5 py-2.5 text-base',
}

/**
 * The `.btn` classes live in index.css so a plain <a> can be styled the same
 * way without wrapping it in a component.
 */
export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  {
    variant = 'primary',
    size = 'md',
    loading = false,
    fullWidth = false,
    icon,
    className,
    disabled,
    children,
    type = 'button',
    ...rest
  },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={cx('btn', VARIANTS[variant], SIZES[size], fullWidth && 'w-full', className)}
      {...rest}
    >
      {loading ? <Spinner size="xs" label={null} /> : icon}
      {children}
    </button>
  )
})

export default Button
