import { Check } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import type { CustomerPills } from "@/lib/types"
import { DOT, TONE, type Tone } from "@/lib/status-tone"
import { cn } from "@/lib/utils"

/**
 * Compact row of status pills derived from `customer.pills`.
 *
 * Visual language matches SessionStatusBadge: outline badge, subtle tinted
 * ring, a small leading dot or icon. Kept tight so several sit in one row.
 */

const PILL = "gap-1.5 px-2 py-0 text-[11px] font-medium"

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
      ? "success"
      : pills.session_status === "expired"
        ? "warning"
        : "critical"

  return (
    <div className={cn("flex flex-wrap items-center gap-1.5", className)}>
      {pills.lifecycle === "logged_in" ? (
        <Badge variant="outline" className={cn(PILL, TONE.success)}>
          <Check className="size-3 shrink-0" />
          Logged in
        </Badge>
      ) : (
        <Pill tone="neutral" label="Created" />
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
        <Pill tone="success" label="Session ✓" dot={false} />
      ) : (
        <Pill tone="neutral" label="No session" dot={false} />
      )}

      {!pills.has_number_token ? (
        <Pill tone="warning" label="No number" />
      ) : null}
    </div>
  )
}
