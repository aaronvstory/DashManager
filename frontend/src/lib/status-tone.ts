/**
 * Semantic status tones — the single source of truth for how status MEANING
 * (success / warning / critical / info / neutral) renders across every badge,
 * pill, and bar in the app. Components import from here instead of hardcoding
 * `text-emerald-500` / `bg-amber-400` / etc., so a future theme swap is a
 * config change in index.css, not a grep-and-replace across ~40 files.
 *
 * Tokens live in index.css (`--status-*` + `--color-status-*` registered in the
 * `@theme inline` block). They were seeded from the previous Tailwind
 * emerald/amber/red/sky values, so adopting these maps is a pure refactor.
 *
 * Color → meaning mapping used during the migration:
 *   emerald → success   amber → warning   red → critical
 *   sky / blue → info   violet (manual flag) → info
 *   orange (remake) → warning   zinc / border → neutral
 */

export type Tone =
  | "success"
  | "warning"
  // Louder warning for the zero-tolerance "⚠ Unconfirmed" case — heavier fill
  // + semibold so it reads as "needs a human", never as done.
  | "warning-strong"
  | "critical"
  | "info"
  | "neutral"

/**
 * Full badge/pill class string per tone: tinted ring + faint fill + readable
 * text. Matches the prior `border-<c>-500/25 bg-<c>-500/10 text-<c>-600
 * dark:text-<c>-400` language exactly (the `-fg` token flips light/dark).
 */
export const TONE: Record<Tone, string> = {
  success:
    "border-status-success/25 bg-status-success/10 text-status-success-fg",
  warning:
    "border-status-warning/25 bg-status-warning/10 text-status-warning-fg",
  "warning-strong":
    "border-status-warning/40 bg-status-warning/15 font-semibold text-status-warning-fg",
  critical:
    "border-status-critical/25 bg-status-critical/10 text-status-critical-fg",
  info: "border-status-info/25 bg-status-info/10 text-status-info-fg",
  neutral: "border-border bg-muted/40 text-muted-foreground",
}

/**
 * Solid dot color per tone (status indicators, freshness bars). Neutral falls
 * back to a muted foreground so it reads as "no signal", not a real color.
 */
export const DOT: Record<Tone, string> = {
  success: "bg-status-success",
  warning: "bg-status-warning",
  "warning-strong": "bg-status-warning",
  critical: "bg-status-critical",
  info: "bg-status-info",
  neutral: "bg-muted-foreground/50",
}
