import { ApiError } from "@/lib/api"
import type { Customer, StrategyName } from "@/lib/types"

/** Display name: "First Last", or a stable placeholder when both are blank. */
export function customerName(c: Customer): string {
  const name = `${c.first_name} ${c.last_name}`.trim()
  return name || `Customer ${c.id}`
}

export function hasRealName(c: Customer): boolean {
  return `${c.first_name} ${c.last_name}`.trim().length > 0
}

/** 'YYYY-MM-DD' → local-midnight Date (new Date(string) would parse as UTC). */
export function parseBucketDate(s: string): Date {
  const [y, m, d] = s.split("-").map(Number)
  return new Date(y || 1970, (m || 1) - 1, d || 1)
}

/** SQLite datetime('now') is 'YYYY-MM-DD HH:MM:SS' in UTC (no zone marker). */
export function parseDbTimestamp(s: string): Date {
  return s.includes("T") ? new Date(s) : new Date(`${s.replace(" ", "T")}Z`)
}

export const STRATEGY_ITEMS: ReadonlyArray<{ label: string; value: StrategyName }> = [
  { label: "Scripted chat", value: "scripted" },
  { label: "LLM chat", value: "llm" },
  { label: "Detect only", value: "none" },
]

/** Pull FastAPI's {"detail": "..."} out of an error, else a fallback. */
export function apiErrorDetail(err: unknown, fallback: string): string {
  if (err instanceof ApiError) {
    try {
      const parsed = JSON.parse(err.body) as { detail?: unknown }
      if (typeof parsed.detail === "string") return parsed.detail
    } catch {
      // body wasn't JSON — fall through
    }
    return err.body || fallback
  }
  if (err instanceof Error && err.message) return err.message
  return fallback
}
