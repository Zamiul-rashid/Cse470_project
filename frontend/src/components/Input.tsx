import { forwardRef, useId, type InputHTMLAttributes, type ReactNode } from 'react'

import { cx } from '../lib/format'

// `prefix` is omitted from the base type on purpose: React declares it as the
// RDFa string attribute, and this component re-uses the name for a ReactNode.
export interface InputProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'size' | 'prefix'> {
  label?: ReactNode
  /** Validation message. Its presence switches the field to the error style. */
  error?: string | null
  /** Static help text, shown only while there is no error. */
  hint?: ReactNode
  /** Small adornment inside the field -- a currency symbol, a search glyph. */
  prefix?: ReactNode
  containerClassName?: string
}

export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  { label, error, hint, prefix, className, containerClassName, id, required, ...rest },
  ref,
) {
  const generatedId = useId()
  const inputId = id ?? generatedId
  const describedBy = error ? `${inputId}-error` : hint ? `${inputId}-hint` : undefined

  return (
    <div className={cx('w-full', containerClassName)}>
      {label ? (
        <label htmlFor={inputId} className="label">
          {label}
          {required ? <span className="ml-0.5 text-red-600">*</span> : null}
        </label>
      ) : null}

      <div className="relative">
        {prefix ? (
          <span className="pointer-events-none absolute inset-y-0 left-0 flex items-center pl-3 text-sm text-slate-400">
            {prefix}
          </span>
        ) : null}
        <input
          ref={ref}
          id={inputId}
          required={required}
          aria-invalid={error ? true : undefined}
          aria-describedby={describedBy}
          className={cx(
            'input',
            prefix && 'pl-8',
            error && 'ring-red-400 focus:ring-red-500',
            className,
          )}
          {...rest}
        />
      </div>

      {error ? (
        <p id={`${inputId}-error`} className="field-error">
          {error}
        </p>
      ) : hint ? (
        <p id={`${inputId}-hint`} className="mt-1.5 text-xs text-slate-500">
          {hint}
        </p>
      ) : null}
    </div>
  )
})

export default Input
