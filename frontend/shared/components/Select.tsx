import { forwardRef, useId, type ReactNode, type SelectHTMLAttributes } from 'react'

import { cx } from '../lib/format'

export interface SelectOption {
  value: string
  label: string
  disabled?: boolean
}

export interface SelectProps extends Omit<SelectHTMLAttributes<HTMLSelectElement>, 'children'> {
  label?: ReactNode
  error?: string | null
  hint?: ReactNode
  /** Declarative option list. Ignored when `children` is given. */
  options?: readonly SelectOption[]
  /** Leading empty option, e.g. "Any department". */
  placeholder?: string
  children?: ReactNode
  containerClassName?: string
}

export const Select = forwardRef<HTMLSelectElement, SelectProps>(function Select(
  {
    label,
    error,
    hint,
    options,
    placeholder,
    children,
    className,
    containerClassName,
    id,
    required,
    ...rest
  },
  ref,
) {
  const generatedId = useId()
  const selectId = id ?? generatedId
  const describedBy = error ? `${selectId}-error` : hint ? `${selectId}-hint` : undefined

  return (
    <div className={cx('w-full', containerClassName)}>
      {label ? (
        <label htmlFor={selectId} className="label">
          {label}
          {required ? <span className="ml-0.5 text-red-600">*</span> : null}
        </label>
      ) : null}

      <div className="relative">
        <select
          ref={ref}
          id={selectId}
          required={required}
          aria-invalid={error ? true : undefined}
          aria-describedby={describedBy}
          className={cx(
            'input appearance-none pr-9',
            error && 'ring-red-400 focus:ring-red-500',
            className,
          )}
          {...rest}
        >
          {placeholder !== undefined ? <option value="">{placeholder}</option> : null}
          {children ??
            options?.map((option) => (
              <option key={option.value} value={option.value} disabled={option.disabled}>
                {option.label}
              </option>
            ))}
        </select>
        <svg
          aria-hidden="true"
          viewBox="0 0 20 20"
          fill="currentColor"
          className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400"
        >
          <path
            fillRule="evenodd"
            d="M5.22 8.22a.75.75 0 0 1 1.06 0L10 11.94l3.72-3.72a.75.75 0 1 1 1.06 1.06l-4.25 4.25a.75.75 0 0 1-1.06 0L5.22 9.28a.75.75 0 0 1 0-1.06Z"
            clipRule="evenodd"
          />
        </svg>
      </div>

      {error ? (
        <p id={`${selectId}-error`} className="field-error">
          {error}
        </p>
      ) : hint ? (
        <p id={`${selectId}-hint`} className="mt-1.5 text-xs text-slate-500">
          {hint}
        </p>
      ) : null}
    </div>
  )
})

export default Select
