import { forwardRef, useId, type ReactNode, type TextareaHTMLAttributes } from 'react'

import { cx } from '../lib/format'

export interface TextAreaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  label?: ReactNode
  error?: string | null
  hint?: ReactNode
  /** Renders a live "123 / 500" counter. Pair with `maxLength`. */
  showCount?: boolean
  containerClassName?: string
}

export const TextArea = forwardRef<HTMLTextAreaElement, TextAreaProps>(function TextArea(
  {
    label,
    error,
    hint,
    showCount = false,
    className,
    containerClassName,
    id,
    required,
    rows = 4,
    maxLength,
    value,
    ...rest
  },
  ref,
) {
  const generatedId = useId()
  const areaId = id ?? generatedId
  const describedBy = error ? `${areaId}-error` : hint ? `${areaId}-hint` : undefined
  const length = typeof value === 'string' ? value.length : 0

  return (
    <div className={cx('w-full', containerClassName)}>
      {label ? (
        <label htmlFor={areaId} className="label">
          {label}
          {required ? <span className="ml-0.5 text-red-600">*</span> : null}
        </label>
      ) : null}

      <textarea
        ref={ref}
        id={areaId}
        rows={rows}
        required={required}
        maxLength={maxLength}
        value={value}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy}
        className={cx(
          'input resize-y leading-relaxed',
          error && 'ring-red-400 focus:ring-red-500',
          className,
        )}
        {...rest}
      />

      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          {error ? (
            <p id={`${areaId}-error`} className="field-error">
              {error}
            </p>
          ) : hint ? (
            <p id={`${areaId}-hint`} className="mt-1.5 text-xs text-slate-500">
              {hint}
            </p>
          ) : null}
        </div>
        {showCount && maxLength ? (
          <p className="mt-1.5 shrink-0 text-xs tabular-nums text-slate-400">
            {length} / {maxLength}
          </p>
        ) : null}
      </div>
    </div>
  )
})

export default TextArea
