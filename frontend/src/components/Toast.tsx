import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import { createPortal } from 'react-dom'

import { errorMessage } from './ErrorBox'
import { cx } from '../lib/format'

export type ToastVariant = 'success' | 'error' | 'info'

export interface ToastOptions {
  title: string
  description?: string
  variant?: ToastVariant
  /** Milliseconds on screen. 0 keeps it until dismissed. Default 5000. */
  duration?: number
}

interface ToastRecord extends ToastOptions {
  id: number
  variant: ToastVariant
}

export interface ToastApi {
  /** Returns the toast id, so it can be dismissed early. */
  toast: (options: ToastOptions) => number
  success: (title: string, description?: string) => number
  error: (title: string, description?: string) => number
  info: (title: string, description?: string) => number
  /** Turns an unknown thrown value into a readable error toast. */
  fromError: (error: unknown, title?: string) => number
  dismiss: (id: number) => void
}

const ToastContext = createContext<ToastApi | null>(null)

const VARIANT_STYLES: Record<ToastVariant, { ring: string; icon: string; path: string }> = {
  success: {
    ring: 'ring-state-approved/25',
    icon: 'text-state-approved',
    path: 'M16.7 6.3a1 1 0 0 1 0 1.4l-6.5 6.5a1 1 0 0 1-1.4 0L5.3 10.7a1 1 0 1 1 1.4-1.4l2.8 2.79 5.8-5.8a1 1 0 0 1 1.4 0Z',
  },
  error: {
    ring: 'ring-state-rejected/25',
    icon: 'text-state-rejected',
    path: 'M10 1.75a8.25 8.25 0 1 0 0 16.5 8.25 8.25 0 0 0 0-16.5ZM10 5a.75.75 0 0 1 .75.75v4.5a.75.75 0 0 1-1.5 0v-4.5A.75.75 0 0 1 10 5Zm0 9.5a1 1 0 1 0 0-2 1 1 0 0 0 0 2Z',
  },
  info: {
    ring: 'ring-brand-500/25',
    icon: 'text-brand-600',
    path: 'M10 1.75a8.25 8.25 0 1 0 0 16.5 8.25 8.25 0 0 0 0-16.5ZM10 6.5a1 1 0 1 1 0-2 1 1 0 0 1 0 2Zm.75 2.25a.75.75 0 0 0-1.5 0v5.5a.75.75 0 0 0 1.5 0v-5.5Z',
  },
}

/**
 * Toast host. Mount once, near the root, inside the router.
 *
 * Use toasts for the outcome of an action the user just took. Failures of a
 * *load* belong in an <ErrorBox> where the missing content would have been.
 */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastRecord[]>([])
  const nextId = useRef(1)
  const timers = useRef(new Map<number, number>())

  const dismiss = useCallback((id: number) => {
    const timer = timers.current.get(id)
    if (timer !== undefined) {
      window.clearTimeout(timer)
      timers.current.delete(id)
    }
    setToasts((current) => current.filter((t) => t.id !== id))
  }, [])

  const toast = useCallback(
    (options: ToastOptions): number => {
      const id = nextId.current++
      const record: ToastRecord = { ...options, id, variant: options.variant ?? 'info' }
      setToasts((current) => [...current, record].slice(-4))

      const duration = options.duration ?? 5000
      if (duration > 0) {
        timers.current.set(
          id,
          window.setTimeout(() => dismiss(id), duration),
        )
      }
      return id
    },
    [dismiss],
  )

  useEffect(() => {
    const pending = timers.current
    return () => {
      pending.forEach((timer) => window.clearTimeout(timer))
      pending.clear()
    }
  }, [])

  const api = useMemo<ToastApi>(
    () => ({
      toast,
      dismiss,
      success: (title, description) => toast({ title, description, variant: 'success' }),
      error: (title, description) => toast({ title, description, variant: 'error' }),
      info: (title, description) => toast({ title, description, variant: 'info' }),
      fromError: (error, title = 'That did not work') =>
        toast({ title, description: errorMessage(error), variant: 'error', duration: 8000 }),
    }),
    [toast, dismiss],
  )

  return (
    <ToastContext.Provider value={api}>
      {children}
      <ToastViewport toasts={toasts} onDismiss={dismiss} />
    </ToastContext.Provider>
  )
}

function ToastViewport({
  toasts,
  onDismiss,
}: {
  toasts: ToastRecord[]
  onDismiss: (id: number) => void
}) {
  if (typeof document === 'undefined' || toasts.length === 0) return null

  return createPortal(
    <div
      aria-live="polite"
      aria-atomic="false"
      className="pointer-events-none fixed inset-x-0 bottom-0 z-[60] flex flex-col items-center gap-2 p-4 sm:inset-x-auto sm:right-0 sm:top-0 sm:items-end"
    >
      {toasts.map((item) => {
        const style = VARIANT_STYLES[item.variant]
        return (
          <div
            key={item.id}
            role="status"
            className={cx(
              'pointer-events-auto w-full max-w-sm animate-slide-up rounded-xl bg-white p-3.5 shadow-pop ring-1',
              style.ring,
            )}
          >
            <div className="flex items-start gap-3">
              <svg
                aria-hidden="true"
                viewBox="0 0 20 20"
                fill="currentColor"
                className={cx('mt-0.5 h-5 w-5 shrink-0', style.icon)}
              >
                <path fillRule="evenodd" clipRule="evenodd" d={style.path} />
              </svg>
              <div className="min-w-0 flex-1">
                <p className="text-sm font-semibold text-slate-900">{item.title}</p>
                {item.description ? (
                  <p className="mt-0.5 break-words text-sm text-slate-600">{item.description}</p>
                ) : null}
              </div>
              <button
                type="button"
                onClick={() => onDismiss(item.id)}
                aria-label="Dismiss notification"
                className="-m-1 rounded-lg p-1 text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-600"
              >
                <svg aria-hidden="true" viewBox="0 0 20 20" fill="currentColor" className="h-4 w-4">
                  <path d="M6.28 5.22a.75.75 0 0 0-1.06 1.06L8.94 10l-3.72 3.72a.75.75 0 1 0 1.06 1.06L10 11.06l3.72 3.72a.75.75 0 1 0 1.06-1.06L11.06 10l3.72-3.72a.75.75 0 0 0-1.06-1.06L10 8.94 6.28 5.22Z" />
                </svg>
              </button>
            </div>
          </div>
        )
      })}
    </div>,
    document.body,
  )
}

export function useToast(): ToastApi {
  const ctx = useContext(ToastContext)
  if (!ctx) throw new Error('useToast must be used inside <ToastProvider>')
  return ctx
}

export default ToastProvider
