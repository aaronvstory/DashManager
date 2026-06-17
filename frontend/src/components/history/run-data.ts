/**
 * Types + tiny helpers for the run history endpoints.
 *
 * GET /api/runs        -> RunsResponse
 * GET /api/runs/{id}   -> RunDetailResponse
 *
 * RunOrderRow mirrors backend db.list_run_orders(): a run_orders row joined
 * onto its orders row (store_name, description, price, order_uuid,
 * order_status). Amount columns are optional — rendered when present.
 */

import { format as formatFns, formatDistanceStrict } from "date-fns"
import type {
  Chat,
  ChatMessage,
  Claim,
  OrderStatus,
  RefundStatus,
  Run,
} from "@/lib/types"

export interface RunsResponse {
  runs: Run[]
}

export interface RunOrderRow {
  id: number
  run_id: number
  order_id: number
  customer_id: number
  refund_status: RefundStatus | null
  error: string | null
  screenshot_path: string | null
  created_at: string
  store_name: string
  description: string
  price: number | null
  order_uuid: string
  order_status: OrderStatus
  /** Not in the join today; rendered if the backend starts including them. */
  total_amount?: number | null
  refund_amount?: number | null
}

export interface ChatWithMessages extends Chat {
  messages: ChatMessage[]
}

export interface RunDetailResponse {
  run: Run
  orders: RunOrderRow[]
  chats: ChatWithMessages[]
  /** Self-claim records (pending_claim orders resolved without a chat). */
  claims?: Claim[]
}

/**
 * One order's full audit trail: the order row + every chat attempt for it
 * (stacked, attempt 1..N) + any self-claim records. This is the per-order
 * transcript view the spec calls for.
 */
export interface OrderAudit {
  order: RunOrderRow
  chats: ChatWithMessages[]
  claims: Claim[]
}

/**
 * Group chats + claims under their order. Chats with no order_id (legacy,
 * customer-keyed) fall back to their first order_ids entry; anything still
 * unmatched is surfaced under a synthetic "unassigned" bucket so nothing is
 * silently dropped from the audit.
 */
export function groupByOrder(detail: RunDetailResponse): {
  perOrder: OrderAudit[]
  orphanChats: ChatWithMessages[]
} {
  const byId = new Map<number, OrderAudit>()
  for (const order of detail.orders) {
    byId.set(order.order_id, { order, chats: [], claims: [] })
  }
  const orphanChats: ChatWithMessages[] = []
  for (const chat of detail.chats) {
    const oid = chat.order_id ?? chat.order_ids[0] ?? null
    const audit = oid != null ? byId.get(oid) : undefined
    if (audit) audit.chats.push(chat)
    else orphanChats.push(chat)
  }
  for (const claim of detail.claims ?? []) {
    byId.get(claim.order_id)?.claims.push(claim)
  }
  // Order the chats within each order by attempt number for a clean stack.
  for (const audit of byId.values()) {
    audit.chats.sort((a, b) => a.attempt_no - b.attempt_no || a.id - b.id)
  }
  // Only orders that actually have chats or claims are "audit" rows.
  const perOrder = [...byId.values()].filter(
    (a) => a.chats.length > 0 || a.claims.length > 0,
  )
  return { perOrder, orphanChats }
}

/** SQLite datetime('now') is UTC without a timezone marker — pin it to UTC. */
export function parseDbDate(value: string): Date {
  const iso = value.includes("T") ? value : value.replace(" ", "T")
  return new Date(/(?:[zZ]|[+-]\d{2}:?\d{2})$/.test(iso) ? iso : `${iso}Z`)
}

/** Format a DB timestamp, tolerating a null/garbled value (an interrupted write
 *  could leave one) — date-fns `format` THROWS on an Invalid Date, which would
 *  crash the whole table. Returns a dash instead. */
export function formatDbDate(value: string | null | undefined,
                             fmt: string): string {
  if (!value) return "—"
  const d = parseDbDate(value)
  return Number.isNaN(d.getTime()) ? "—" : formatFns(d, fmt)
}

const usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" })

export function money(value: number | null | undefined): string | null {
  return typeof value === "number" && Number.isFinite(value) ? usd.format(value) : null
}

/** 'Bucket 2026-06-11' | 'N customers' | '—' */
export function scopeSummary(scope: Record<string, unknown>): string {
  const bucket = scope["bucket_date"]
  if (typeof bucket === "string" && bucket) return `Bucket ${bucket}`
  const ids = scope["customer_ids"]
  if (Array.isArray(ids)) return `${ids.length} customer${ids.length === 1 ? "" : "s"}`
  return "—"
}

/** Safe numeric read from the loosely-typed stats blob. */
export function statNum(stats: Record<string, unknown>, key: string): number {
  const value = stats[key]
  return typeof value === "number" ? value : 0
}

/** Human duration ('4 minutes') for finished runs, null while running. */
export function runDuration(run: Run): string | null {
  if (!run.finished_at) return null
  try {
    return formatDistanceStrict(parseDbDate(run.finished_at), parseDbDate(run.started_at))
  } catch {
    return null
  }
}

/** Last path segment of a screenshot path (handles \ and /). */
export function basename(path: string): string {
  const parts = path.split(/[\\/]+/)
  return parts[parts.length - 1] || path
}
