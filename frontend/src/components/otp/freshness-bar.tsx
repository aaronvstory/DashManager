import { useEffect, useState } from "react"
import { cn } from "@/lib/utils"

/** api.cc codes expire ~30s after they arrive. */
export const OTP_POLL_MS = 5000
export const OTP_CODE_TTL_MS = 30000

/**
 * A shrinking bar that counts the ~30s api.cc code lifetime down from the last
 * fetch. Purely visual freshness cue — the next poll replaces the code anyway.
 * Shared by the Bucket and Batch OTP views.
 */
export function FreshnessBar({
  fetchedAt,
  paused,
}: {
  fetchedAt: string | null
  paused: boolean
}) {
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    if (paused) return
    const id = setInterval(() => setNow(Date.now()), 500)
    return () => clearInterval(id)
  }, [paused])

  // While paused the ticking interval is off, so a manual refresh (new
  // fetchedAt) would otherwise leave `now` stale and the bar frozen. Re-sync
  // `now` whenever fetchedAt changes so the freshness reflects the new code.
  useEffect(() => {
    setNow(Date.now())
  }, [fetchedAt])

  // Guard a malformed timestamp (getTime() → NaN) so width/label never render NaN.
  const parsed = fetchedAt ? new Date(fetchedAt).getTime() : now
  const base = Number.isFinite(parsed) ? parsed : now
  const elapsed = Math.max(0, now - base)
  const pct = Math.max(0, Math.min(1, 1 - elapsed / OTP_CODE_TTL_MS))
  const stale = pct <= 0

  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-20 overflow-hidden bg-muted">
        <div
          className={cn(
            "h-full transition-[width] duration-500 ease-linear",
            stale ? "bg-zinc-500" : pct < 0.34 ? "bg-amber-500" : "bg-emerald-500",
          )}
          style={{ width: `${pct * 100}%` }}
        />
      </div>
      <span className="num text-[0.7rem] text-muted-foreground">
        {stale ? "expiring" : `${Math.ceil((pct * OTP_CODE_TTL_MS) / 1000)}s`}
      </span>
    </div>
  )
}
