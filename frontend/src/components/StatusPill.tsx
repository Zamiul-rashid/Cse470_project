import { statusAccent, statusColor, statusLabel, type AnyStatus, type StatusKind } from '../lib/format'
import { Pill } from './Pill'

export interface StatusPillProps {
  status: AnyStatus
  /**
   * Which vocabulary the status belongs to. Only needed to disambiguate
   * `OPEN`, which means "looking for this" on a request and "awaiting review"
   * on a report.
   */
  kind?: StatusKind
  /** Adds a solid dot in the status colour. */
  withDot?: boolean
  className?: string
}

/** Status chip for listings, transactions, requests and reports. */
export function StatusPill({ status, kind, withDot = true, className }: StatusPillProps) {
  return (
    <Pill
      color={statusColor(status, kind)}
      dot={withDot ? statusAccent(status, kind) : undefined}
      className={className}
    >
      {statusLabel(status, kind)}
    </Pill>
  )
}

export default StatusPill
