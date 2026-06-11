/**
 * Frontend mirror of the backend contract in backend/models.py.
 *
 * Keys are snake_case because that is exactly what the API returns.
 * If models.py changes, this file must change with it.
 */

// ---------------------------------------------------------------------------
// Enums (StrEnum values on the backend → string unions here)
// ---------------------------------------------------------------------------

export type SessionStatus = "active" | "expired" | "invalid"

export type OrderStatus = "active" | "cancelled"

export type RefundStatus =
  | "unchecked"
  | "refunded" // Refund line present, amount >= total
  | "partial" // Refund line present, 0 < amount < total
  | "not_refunded" // no Refund line — pursue, even if "canceled"
  | "unknown" // unparseable — never silently pass

export type RunStatus = "running" | "completed" | "stopped" | "error"

export type ChatOutcome =
  | "success" // agent confirmed refund to ORIGINAL payment method
  | "failed"
  | "blocked" // silent rate-limit block
  | "review_blocked" // double "Got it" popup
  | "manual_flag" // needs a human follow-up; transcript saved

export type StrategyName = "scripted" | "llm" | "none"

export type ChatDirection = "out" | "in" | "system"

// ---------------------------------------------------------------------------
// Entities
// ---------------------------------------------------------------------------

export interface Customer {
  id: number
  first_name: string
  last_name: string
  email: string
  phone: string
  /** 'YYYY-MM-DD', user-assigned (defaults to date added) */
  bucket_date: string
  storage_state_path: string
  cookies_path: string | null
  session_status: SessionStatus
  created_at: string
  notes: string
}

/** Raw result of scraping one card on https://www.doordash.com/orders. */
export interface ScrapedOrder {
  order_uuid: string
  receipt_url: string
  store_name: string
  description: string
  items_count: number | null
  price: number | null
  order_status: OrderStatus
}

export interface Order {
  id: number
  customer_id: number
  order_uuid: string
  receipt_url: string
  store_name: string
  description: string
  items_count: number | null
  price: number | null
  order_status: OrderStatus
  refund_status: RefundStatus
  total_amount: number | null
  refund_amount: number | null
  last_checked_at: string | null
}

export interface Run {
  id: number
  started_at: string
  finished_at: string | null
  scope: Record<string, unknown>
  chat_strategy: StrategyName
  status: RunStatus
  stats: Record<string, unknown>
}

export interface Chat {
  id: number
  run_id: number
  customer_id: number
  order_ids: number[]
  opening_message: string
  outcome: ChatOutcome | null
  agent_reached: boolean
  started_at: string
  finished_at: string | null
}

/** Mirrors ChatMessageRow on the backend. */
export interface ChatMessage {
  id: number
  chat_id: number
  ts: string
  direction: ChatDirection
  content: string
}

// ---------------------------------------------------------------------------
// SSE events (mirrors EVENT_TYPES + Event in models.py)
// ---------------------------------------------------------------------------

export type EventType =
  | "login_waiting"
  | "login_captured"
  | "login_failed"
  | "account_balance"
  | "identity_generating"
  | "identity_generated"
  | "number_renting"
  | "number_rented"
  | "signup_submitting"
  | "signup_outcome"
  | "otp_waiting"
  | "otp_received"
  | "otp_resent"
  | "account_created"
  | "account_failed"
  | "run_started"
  | "customer_started"
  | "session_invalid"
  | "orders_found"
  | "order_checking"
  | "order_checked"
  | "chat_opened"
  | "chat_escalation"
  | "chat_message"
  | "chat_outcome"
  | "customer_done"
  | "run_done"
  | "run_error"
  | "log"
  | "heartbeat"

/**
 * Runtime mirror of EVENT_TYPES in backend/models.py. The backend emits
 * NAMED SSE events (`event: <type>`), so the EventSource needs an explicit
 * addEventListener per type — keep this list in sync with the backend.
 */
export const EVENT_TYPES: readonly EventType[] = [
  "login_waiting",
  "login_captured",
  "login_failed",
  "account_balance",
  "identity_generating",
  "identity_generated",
  "number_renting",
  "number_rented",
  "signup_submitting",
  "signup_outcome",
  "otp_waiting",
  "otp_received",
  "otp_resent",
  "account_created",
  "account_failed",
  "run_started",
  "customer_started",
  "session_invalid",
  "orders_found",
  "order_checking",
  "order_checked",
  "chat_opened",
  "chat_escalation",
  "chat_message",
  "chat_outcome",
  "customer_done",
  "run_done",
  "run_error",
  "log",
  "heartbeat",
]

export interface AppEvent {
  id: number
  ts: string
  run_id: number | null
  type: EventType
  data: Record<string, unknown>
}
