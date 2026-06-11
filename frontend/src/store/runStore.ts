import { create } from "zustand"
import type {
  AppEvent,
  ChatDirection,
  ChatOutcome,
  OrderStatus,
  RefundStatus,
  StrategyName,
} from "@/lib/types"

/** Cap the in-memory live log so a long run can't grow unbounded. */
const LOG_LIMIT = 500

// ---------------------------------------------------------------------------
// Live-run state shapes (built up from SSE events, mirrors backend/runner.py)
// ---------------------------------------------------------------------------

/** Snapshot dict from run_started/customer_done/run_done `data.stats`. */
export type RunStats = Record<string, number>

export interface CustomerProgress {
  id: number
  name: string
  position: number
  total: number
  done: boolean
  sessionInvalid: boolean
}

export interface LiveOrder {
  order_id: number
  store: string
  url: string
  /** scrape extras — rendered when the backend includes them in the event */
  price?: number | null
  items_count?: number | null
  order_status?: OrderStatus
  refund_status?: RefundStatus
  total_amount?: number | null
  refund_amount?: number | null
  /** true between order_checking and order_checked */
  checking: boolean
}

export interface LiveChatMessage {
  direction: ChatDirection
  content: string
  /** synthetic centered marker injected on chat_escalation events */
  escalation?: boolean
}

export interface LiveChat {
  chat_id: number
  customer_id: number
  order_ids: number[]
  messages: LiveChatMessage[]
  outcome?: ChatOutcome
  agent_reached?: boolean
  escalations: number
}

interface RunState {
  /** EventSource currently open to /api/events. */
  connected: boolean
  /** Every event flows through here — login flows etc. watch this. */
  lastEvent: AppEvent | null
  runActive: boolean
  runId: number | null
  startedScope: Record<string, unknown> | null
  chatStrategy: StrategyName | null
  /** Latest stats snapshot (run_started clears, customer_done/run_done set). */
  stats: RunStats
  customersProgress: CustomerProgress[]
  orders: Record<number, LiveOrder>
  chats: Record<number, LiveChat>
  liveLog: AppEvent[]

  setConnected: (connected: boolean) => void
  /** Adopt a run discovered via GET /api/runs/active (page opened mid-run). */
  setActiveRun: (runId: number | null) => void
  /** Reducer for every incoming SSE event. */
  applyEvent: (ev: AppEvent) => void
  /** Reset all run-scoped state (called automatically on run_started). */
  clearRun: () => void
  clearLog: () => void
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const EMPTY_RUN = {
  runActive: false,
  runId: null as number | null,
  startedScope: null as Record<string, unknown> | null,
  chatStrategy: null as StrategyName | null,
  stats: {} as RunStats,
  customersProgress: [] as CustomerProgress[],
  orders: {} as Record<number, LiveOrder>,
  chats: {} as Record<number, LiveChat>,
}

function num(v: unknown): number | null {
  return typeof v === "number" ? v : null
}

function str(v: unknown, fallback = ""): string {
  return typeof v === "string" ? v : fallback
}

/** The chat currently in flight (chat_escalation events carry no chat_id). */
function latestChatId(chats: Record<number, LiveChat>): number | null {
  const ids = Object.keys(chats).map(Number)
  return ids.length ? Math.max(...ids) : null
}

function markCustomer(
  list: CustomerProgress[],
  customerId: number,
  patch: Partial<CustomerProgress>,
): CustomerProgress[] {
  return list.map((c) => (c.id === customerId ? { ...c, ...patch } : c))
}

// ---------------------------------------------------------------------------
// Store
// ---------------------------------------------------------------------------

export const useRunStore = create<RunState>((set) => ({
  connected: false,
  lastEvent: null,
  liveLog: [],
  ...EMPTY_RUN,

  setConnected: (connected) => set({ connected }),

  setActiveRun: (runId) =>
    set({ runId, runActive: runId !== null }),

  applyEvent: (ev) =>
    set((state) => {
      // Heartbeats are pure liveness — don't churn lastEvent/log subscribers.
      if (ev.type === "heartbeat") return state

      const base: Partial<RunState> = {
        lastEvent: ev,
        liveLog: [...state.liveLog, ev].slice(-LOG_LIMIT),
      }
      const d = ev.data

      switch (ev.type) {
        case "run_started":
          // Fresh run: wipe everything scoped to the previous one.
          return {
            ...base,
            ...EMPTY_RUN,
            runActive: true,
            runId: ev.run_id,
            startedScope: (d.scope as Record<string, unknown>) ?? {},
            chatStrategy: (d.chat_strategy as StrategyName) ?? null,
          }

        case "customer_started": {
          const id = num(d.customer_id) ?? -1
          const entry: CustomerProgress = {
            id,
            name: str(d.name, `Customer ${id}`),
            position: num(d.position) ?? state.customersProgress.length + 1,
            total: num(d.total) ?? 0,
            done: false,
            sessionInvalid: false,
          }
          const exists = state.customersProgress.some((c) => c.id === id)
          return {
            ...base,
            customersProgress: exists
              ? markCustomer(state.customersProgress, id, entry)
              : [...state.customersProgress, entry],
          }
        }

        case "session_invalid": {
          const id = num(d.customer_id) ?? -1
          // The runner skips straight to the next customer (no customer_done).
          return {
            ...base,
            customersProgress: markCustomer(state.customersProgress, id, {
              sessionInvalid: true,
              done: true,
            }),
          }
        }

        case "orders_found":
          return base

        case "order_checking": {
          const id = num(d.order_id) ?? -1
          const prev = state.orders[id]
          return {
            ...base,
            orders: {
              ...state.orders,
              [id]: {
                ...prev,
                order_id: id,
                store: str(d.store, prev?.store ?? ""),
                url: str(d.url, prev?.url ?? ""),
                price: num(d.price) ?? prev?.price,
                items_count: num(d.items_count) ?? prev?.items_count,
                order_status:
                  (d.order_status as OrderStatus | undefined) ??
                  prev?.order_status,
                checking: true,
              },
            },
          }
        }

        case "order_checked": {
          const id = num(d.order_id) ?? -1
          const prev = state.orders[id]
          return {
            ...base,
            orders: {
              ...state.orders,
              [id]: {
                ...prev,
                order_id: id,
                store: prev?.store ?? "",
                url: prev?.url ?? "",
                refund_status: d.refund_status as RefundStatus | undefined,
                total_amount: num(d.total_amount),
                refund_amount: num(d.refund_amount),
                checking: false,
              },
            },
          }
        }

        case "chat_opened": {
          const id = num(d.chat_id) ?? -1
          return {
            ...base,
            chats: {
              ...state.chats,
              [id]: {
                chat_id: id,
                customer_id: num(d.customer_id) ?? -1,
                order_ids: Array.isArray(d.order_ids)
                  ? (d.order_ids as number[])
                  : [],
                messages: [],
                escalations: 0,
              },
            },
          }
        }

        case "chat_escalation": {
          // No chat_id in the payload — attribute to the chat in flight.
          const id = latestChatId(state.chats)
          if (id === null) return base
          const chat = state.chats[id]
          const attempt = num(d.attempt) ?? chat.escalations + 1
          return {
            ...base,
            chats: {
              ...state.chats,
              [id]: {
                ...chat,
                escalations: attempt,
                messages: [
                  ...chat.messages,
                  {
                    direction: "system" as const,
                    content: `Escalation attempt ${attempt}`,
                    escalation: true,
                  },
                ],
              },
            },
          }
        }

        case "chat_message": {
          const id = num(d.chat_id) ?? -1
          const chat = state.chats[id] ?? {
            chat_id: id,
            customer_id: -1,
            order_ids: [],
            messages: [],
            escalations: 0,
          }
          return {
            ...base,
            chats: {
              ...state.chats,
              [id]: {
                ...chat,
                messages: [
                  ...chat.messages,
                  {
                    direction: (d.direction as ChatDirection) ?? "system",
                    content: str(d.content),
                  },
                ],
              },
            },
          }
        }

        case "chat_outcome": {
          const id = num(d.chat_id) ?? -1
          const chat = state.chats[id]
          if (!chat) return base
          return {
            ...base,
            chats: {
              ...state.chats,
              [id]: {
                ...chat,
                outcome: (d.outcome as ChatOutcome | null) ?? undefined,
                agent_reached: Boolean(d.agent_reached),
              },
            },
          }
        }

        case "customer_done": {
          const id = num(d.customer_id) ?? -1
          return {
            ...base,
            customersProgress: markCustomer(state.customersProgress, id, {
              done: true,
            }),
            stats: (d.stats as RunStats) ?? state.stats,
          }
        }

        case "run_done":
          return {
            ...base,
            runActive: false,
            stats: (d.stats as RunStats) ?? state.stats,
          }

        case "run_error":
          return { ...base, runActive: false }

        // Login / relogin flow + plain log lines: surface via lastEvent / liveLog only.
        case "log":
        case "login_waiting":
        case "login_captured":
        case "login_failed":
        case "relogin_started":
        case "relogin_outcome":
        case "relogin_done":
        case "relogin_failed":
          return base

        default:
          return base
      }
    }),

  clearRun: () => set({ ...EMPTY_RUN }),

  clearLog: () => set({ liveLog: [], lastEvent: null }),
}))
