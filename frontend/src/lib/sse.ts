import { useEffect } from "react"
import { useRunStore } from "@/store/runStore"
import { EVENT_TYPES, type AppEvent } from "@/lib/types"

/**
 * Subscribes to the backend SSE stream at /api/events and dispatches every
 * parsed event into the run store. Mount once (the app layout does this).
 *
 * The backend emits NAMED events (`event: <type>` lines), which never reach
 * EventSource.onmessage — that callback only fires for unnamed frames. So we
 * attach one shared listener per known event type (mirroring backend
 * models.py EVENT_TYPES) and keep onmessage as a fallback for any unnamed
 * frames.
 *
 * EventSource reconnects automatically after errors, so no manual retry
 * logic is needed — we just reflect connection state into the store.
 */
export function useEvents(): void {
  const applyEvent = useRunStore((s) => s.applyEvent)
  const setConnected = useRunStore((s) => s.setConnected)

  useEffect(() => {
    const source = new EventSource("/api/events")

    source.onopen = () => setConnected(true)
    source.onerror = () => setConnected(false) // native auto-reconnect kicks in

    const handle = (e: MessageEvent<string>) => {
      setConnected(true)
      try {
        applyEvent(JSON.parse(e.data) as AppEvent)
      } catch {
        // Malformed payload — ignore rather than crash the stream.
      }
    }

    for (const type of EVENT_TYPES) {
      if (type === "heartbeat") continue
      source.addEventListener(type, handle)
    }

    // Heartbeats carry an empty payload ("{}") — skip the content entirely
    // and use them purely as a liveness signal.
    source.addEventListener("heartbeat", () => setConnected(true))

    // Fallback: any unnamed `data:` frame still lands here.
    source.onmessage = handle

    return () => {
      source.close()
      setConnected(false)
    }
  }, [applyEvent, setConnected])
}
