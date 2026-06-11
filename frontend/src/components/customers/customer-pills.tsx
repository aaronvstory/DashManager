import { Check } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import type { CustomerPills } from "@/lib/types"
import { cn } from "@/lib/utils"

/**
 * Compact row of status pills derived from `customer.pills`.
 *
 * Visual language matches SessionStatusBadge: outline badge, subtle tinted
 * ring, a small leading dot or icon. Kept tight so several sit in one row.
 */

const PILL = "gap-1.5 px-2 py-0 text-[11px] font-medium"

const TONE = {
  emerald:
    "border-emerald-500/25 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
  amber:
    "border-amber-500/25 bg-amber-500/10 text-amber-600 dark:text-amber-400",
  red: "border-red-500/25 bg-red-500/10 text-red-600 dark:text-red-400",
  zinc: "border-border bg-muted/40 text-muted-foreground",
} as const

const DOT = {
  emerald: "bg-emerald-500",
  amber: "bg-amber-500",
  red: "bg-red-500",
  zinc: "bg-muted-foreground/50",
} as const

type Tone = keyof typeof TONE

function Pill({
  tone,
  label,
  dot = true,
}: {
  tone: Tone
  label: string
  dot?: boolean
}) {
  return (
    <Badge variant="outline" className={cn(PILL, TONE[tone])}>
      {dot ? (
        <span
          aria-hidden
          className={cn("size-1.5 shrink-0 rounded-full", DOT[tone])}
        />
      ) : null}
      {label}
    </Badge>
  )
}

export function CustomerPills({
  pills,
  className,
}: {
  pills: CustomerPills
  className?: string
}) {
  const sessionTone: Tone =
    pills.session_status === "active"
      ? "emerald"
      : pills.session_status === "expired"
        ? "amber"
        : "red"

  return (
    <div className={cn("flex flex-wrap items-center gap-1.5", className)}>
      {pills.lifecycle === "logged_in" ? (
        <Badge variant="outline" className={cn(PILL, TONE.emerald)}>
          <Check className="size-3 shrink-0" />
          Logged in
        </Badge>
      ) : (
        <Pill tone="zinc" label="Created" />
      )}

      <Pill
        tone={sessionTone}
        label={
          pills.session_status === "active"
            ? "Active"
            : pills.session_status === "expired"
              ? "Expired"
              : "Invalid"
        }
      />

      {pills.has_session ? (
        <Pill tone="emerald" label="Session ✓" dot={false} />
      ) : (
        <Pill tone="zinc" label="No session" dot={false} />
      )}

      {!pills.has_number_token ? <Pill tone="amber" label="No number" /> : null}
    </div>
  )
}
