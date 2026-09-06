import { cx } from '../lib/format'

export interface PaginationProps {
  /** 1-based. */
  page: number
  /** Total number of pages; `Page<T>.pages` straight off the API. */
  pages: number
  onPageChange: (page: number) => void
  /** Shown as "1-20 of 137" when both are given. */
  total?: number
  pageSize?: number
  className?: string
}

/** Page numbers to render, with `null` standing in for an ellipsis. */
function pageWindow(page: number, pages: number): (number | null)[] {
  if (pages <= 7) return Array.from({ length: pages }, (_, i) => i + 1)

  const items: (number | null)[] = [1]
  const start = Math.max(2, page - 1)
  const end = Math.min(pages - 1, page + 1)

  if (start > 2) items.push(null)
  for (let i = start; i <= end; i += 1) items.push(i)
  if (end < pages - 1) items.push(null)
  items.push(pages)
  return items
}

const arrowClasses =
  'inline-flex h-8 items-center rounded-lg px-2.5 text-sm font-medium text-slate-600 ring-1 ring-inset ring-slate-300 transition-colors hover:bg-slate-50 disabled:pointer-events-none disabled:opacity-40'

export function Pagination({ page, pages, onPageChange, total, pageSize, className }: PaginationProps) {
  if (pages <= 1) return null

  const first = pageSize ? (page - 1) * pageSize + 1 : null
  const last = pageSize && total !== undefined ? Math.min(page * pageSize, total) : null

  return (
    <nav
      aria-label="Pagination"
      className={cx('flex flex-wrap items-center justify-between gap-3', className)}
    >
      <p className="text-xs text-slate-500">
        {first !== null && last !== null && total !== undefined
          ? `Showing ${first}-${last} of ${total}`
          : `Page ${page} of ${pages}`}
      </p>

      <div className="flex items-center gap-1">
        <button
          type="button"
          className={arrowClasses}
          disabled={page <= 1}
          onClick={() => onPageChange(page - 1)}
        >
          Previous
        </button>

        {pageWindow(page, pages).map((item, index) =>
          item === null ? (
            <span key={`gap-${index}`} className="px-1.5 text-sm text-slate-400">
              &hellip;
            </span>
          ) : (
            <button
              key={item}
              type="button"
              aria-current={item === page ? 'page' : undefined}
              onClick={() => onPageChange(item)}
              className={cx(
                'inline-flex h-8 min-w-8 items-center justify-center rounded-lg px-2 text-sm font-medium tabular-nums transition-colors',
                item === page
                  ? 'bg-brand-600 text-white'
                  : 'text-slate-600 hover:bg-slate-100 hover:text-slate-900',
              )}
            >
              {item}
            </button>
          ),
        )}

        <button
          type="button"
          className={arrowClasses}
          disabled={page >= pages}
          onClick={() => onPageChange(page + 1)}
        >
          Next
        </button>
      </div>
    </nav>
  )
}

export default Pagination
