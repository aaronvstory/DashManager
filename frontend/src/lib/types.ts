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

/**
 * Order lifecycle as surfaced by the DB viewer (/customers/full). The scraper
 * stores the narrower `OrderStatus` ("active" | "cancelled"), but the enriched
 * full view can also report DoorDash live states.
 */
export type OrderLifecycle = "in_progress" | "completed" | "cancelled" | "active"

export type OrderStatus = "active" | "cancelled"

/** Customer lifecycle: created via the account flow vs. fully logged in. */
export type CustomerLifecycle = "created" | "logged_in"

export type RefundStatus =
  | "unchecked"
  | "refunded" // Refund line present, amount >= total
  | "partial" // Refund line present, 0 < amount < total
  | "pending_claim" // self-service "Choose your refund method" — claim to original card
  | "not_refunded" // no Refund line — pursue, even if "canceled"
  | "remake" // DoorDash remade it without being asked; usually no auto-refund
  | "unconfirmed" // ZERO-TOLERANCE: action ran but NOT proven to the card — needs human
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

/**
 * Derived status flags the backend attaches to each customer for the UI.
 * Mirrors the `pills` object on the /customers and /customers/full responses.
 */
export interface CustomerPills {
  lifecycle: CustomerLifecycle
  session_status: SessionStatus
  has_session: boolean
  has_profile: boolean
  has_storage_backup: boolean
  has_number_token: boolean
}

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
  /** Daisy number token; '' when the customer wasn't created via the account flow. */
  number_token?: string
  /** Derived status flags for the UI (present on /customers and /customers/full). */
  pills?: CustomerPills
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
  order_status: OrderLifecycle
  refund_status: RefundStatus
  total_amount: number | null
  refund_amount: number | null
  /** Live status copy, e.g. "Heading to you". Present on the /customers/full view. */
  status_text?: string | null
  /** Assigned dasher's first name, e.g. "Erin". Present on the /customers/full view. */
  dasher_name?: string | null
  last_checked_at: string | null
  /** Per-order audit trail — present on the enriched /customers/full view. */
  chats?: Chat[]
  claims?: Claim[]
  /** HOW the refund happened + the proof line (derived server-side). */
  resolution?: OrderResolution
}

/**
 * Derived on the backend (report.resolution_method): the category of how an
 * order's refund was achieved, plus the confirming proof line.
 */
export interface OrderResolution {
  /** "Self-claim" | "Agent chat" | "Credits→card (agent chat)" | "Self-serve chat" | "Already refunded" | "Pending" | "—" */
  label: string
  /** Short proof: claim amount/destination, or the agent's confirming line. */
  confirmation: string
}

/** A customer enriched with derived pills + their orders, from /customers/full. */
export interface FullCustomer extends Customer {
  pills: CustomerPills
  orders: Order[]
}

// ---------------------------------------------------------------------------
// Request shapes that accept a per-action headless override
// ---------------------------------------------------------------------------

/** Common opt-in: omit/null = use the global browser.headless setting. */
export interface HeadlessOverride {
  headless?: boolean
}

export interface CreateAccountRequest extends HeadlessOverride {
  bucket_date: string
  location_origin?: string
  radius_miles?: number
}

export type ReloginRequest = HeadlessOverride

export type TestSessionRequest = HeadlessOverride

export interface RunRequest extends HeadlessOverride {
  scope: Record<string, unknown>
  chat_strategy: StrategyName
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
  /** The order this chat belongs to (V5 — chats are now order-keyed). */
  order_id: number | null
  /** Retry attempt number on that order (1..3). */
  attempt_no: number
  order_ids: number[]
  opening_message: string
  outcome: ChatOutcome | null
  agent_reached: boolean
  started_at: string
  finished_at: string | null
  /** Per-order transcript: messages attached by the run-detail endpoint. */
  messages?: ChatMessage[]
}

/** Mirrors ChatMessageRow on the backend. */
export interface ChatMessage {
  id: number
  chat_id: number
  ts: string
  direction: ChatDirection
  content: string
}

/**
 * One self-claim attempt on a pending_claim order (mirrors ClaimRecord /
 * the `claims` table). A claim resolves a refund WITHOUT an agent chat.
 */
export interface Claim {
  id: number
  run_id: number
  order_id: number
  customer_id: number
  amount: number | null
  to_original_payment: boolean
  confirmed: boolean
  outcome: string // 'success' | 'failed' | 'wrong_method' | 'error'
  error: string | null
  created_at: string
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
  | "relogin_started"
  | "relogin_outcome"
  | "relogin_done"
  | "relogin_failed"
  | "run_started"
  | "customer_started"
  | "session_invalid"
  | "orders_found"
  | "order_checking"
  | "order_checked"
  | "claim_started"
  | "claim_outcome"
  | "chat_opened"
  | "chat_escalation"
  | "chat_message"
  | "chat_attempt"
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
  "relogin_started",
  "relogin_outcome",
  "relogin_done",
  "relogin_failed",
  "run_started",
  "customer_started",
  "session_invalid",
  "orders_found",
  "order_checking",
  "order_checked",
  "claim_started",
  "claim_outcome",
  "chat_opened",
  "chat_escalation",
  "chat_message",
  "chat_attempt",
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
