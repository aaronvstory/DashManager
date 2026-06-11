import { useEffect } from "react"
import { useRunStore } from "@/store/runStore"
import type { AppEvent } from "@/lib/types"

/**
 * Subscribes to the backend SSE stream at /api/events and dispatches every
 * parsed event into the run store. Mount once (the app layout does this).
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

    source.onmessage = (e: MessageEvent<string>) => {
      try {
        applyEvent(JSON.parse(e.data) as AppEvent)
      } catch {
        // Malformed payload — ignore rather than crash the stream.
      }
    }

    return () => {
      source.close()
      setConnected(false)
    }
  }, [applyEvent, setConnected])
}
