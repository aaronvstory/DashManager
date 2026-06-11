import { create } from "zustand"
import type { AppEvent } from "@/lib/types"

/** Cap the in-memory live log so a long run can't grow unbounded. */
const LOG_LIMIT = 500

interface RunState {
  /** EventSource currently open to /api/events. */
  connected: boolean
  lastEvent: AppEvent | null
  runActive: boolean
  liveLog: AppEvent[]
  setConnected: (connected: boolean) => void
  /**
   * Reducer for every incoming SSE event.
   * Stub for now — later waves extend this with per-type handling
   * (orders_found, chat_message, customer_done, ...).
   */
  applyEvent: (ev: AppEvent) => void
  clearLog: () => void
}

export const useRunStore = create<RunState>((set) => ({
  connected: false,
  lastEvent: null,
  runActive: false,
  liveLog: [],

  setConnected: (connected) => set({ connected }),

  applyEvent: (ev) =>
    set((state) => ({
      lastEvent: ev,
      liveLog:
        ev.type === "heartbeat"
          ? state.liveLog
          : [...state.liveLog, ev].slice(-LOG_LIMIT),
      runActive:
        ev.type === "run_started"
          ? true
          : ev.type === "run_done" || ev.type === "run_error"
            ? false
            : state.runActive,
    })),

  clearLog: () => set({ liveLog: [], lastEvent: null }),
}))
