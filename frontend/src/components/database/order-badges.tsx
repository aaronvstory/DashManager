import {
  BadgeCheck,
  CreditCard,
  Hand,
  Hourglass,
  MessageSquareText,
  Minus,
  Truck,
  type LucideIcon,
} from "lucide-react"
import { Badge } from "@/components/ui/badge"
import type { OrderLifecycle, RefundStatus } from "@/lib/types"
import { cn } from "@/lib/utils"

/**
 * Order + refund status badges for the Database viewer. Same outline + tinted
 * ring language as the customer pills, so the page reads as one family.
 */

const TONE = {
  blue: "border-blue-500/25 bg-blue-500/10 text-blue-600 dark:text-blue-400",
  emerald:
    "border-emerald-500/25 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
  amber:
    "border-amber-500/25 bg-amber-500/10 text-amber-600 dark:text-amber-400",
  // Stronger amber for the zero-tolerance ⚠ Unconfirmed warning — matches the
  // emphasis in run/badges.tsx + history/badges.tsx so the warning reads with
  // the same urgency in every view.
  "alert-amber":
    "border-amber-500/40 bg-amber-500/15 font-semibold text-amber-600 dark:text-amber-400",
  red: "border-red-500/25 bg-red-500/10 text-red-600 dark:text-red-400",
  zinc: "border-border bg-muted/40 text-muted-foreground",
} as const

type Tone = keyof typeof TONE

const ORDER_LABEL: Record<OrderLifecycle, { tone: Tone; label: string }> = {
  in_progress: { tone: "blue", label: "In progress" },
  active: { tone: "blue", label: "In progress" },
  completed: { tone: "zinc", label: "Completed" },
  cancelled: { tone: "red", label: "Cancelled" },
}

const REFUND_LABEL: Record<RefundStatus, { tone: Tone; label: string }> = {
  refunded: { tone: "emerald", label: "Refunded" },
  partial: { tone: "amber", label: "Partial refund" },
  pending_claim: { tone: "blue", label: "Self-claim" },
  not_refunded: { tone: "red", label: "Not refunded" },
  remake: { tone: "amber", label: "Remake" },
  // ZERO-TOLERANCE: an action ran but we could NOT prove it landed on the
  // card. Loud amber warning — must read as "needs a human", never as done.
  unconfirmed: { tone: "alert-amber", label: "⚠ Unconfirmed" },
  unchecked: { tone: "zinc", label: "Unchecked" },
  unknown: { tone: "amber", label: "Unknown" },
}

export function OrderStatusBadge({
  status,
  statusText,
  dasherName,
}: {
  status: OrderLifecycle
  statusText?: string | null
  dasherName?: string | null
}) {
  const { tone, label } = ORDER_LABEL[status] ?? ORDER_LABEL.completed
  const live = status === "in_progress" || status === "active"
  // Live orders surface "Dasher · status copy", e.g. "Erin · Heading to you".
  const detail = live
    ? [dasherName, statusText].filter(Boolean).join(" · ")
    : ""

  return (
    <span className="inline-flex flex-wrap items-center gap-1.5">
      <Badge variant="outline" className={cn("gap-1.5", TONE[tone])}>
        {live ? (
          <Truck className="size-3 shrink-0" />
        ) : (
          <span
            aria-hidden
            className={cn(
              "size-1.5 shrink-0 rounded-full",
              tone === "red" ? "bg-red-500" : "bg-muted-foreground/50",
            )}
          />
        )}
        {label}
      </Badge>
      {detail ? (
        <span className="text-xs text-muted-foreground">{detail}</span>
      ) : null}
    </span>
  )
}

/**
 * HOW the refund was achieved — the resolution.label from the backend. Keyed
 * on the exact labels emitted by report.resolution_method so the customer view
 * reads the same vocabulary as the daily report.
 */
const METHOD_STYLE: Record<string, { tone: Tone; icon: LucideIcon }> = {
  "Self-claim": { tone: "blue", icon: Hand },
  "Agent chat": { tone: "emerald", icon: MessageSquareText },
  "Credits→card (agent chat)": { tone: "emerald", icon: CreditCard },
  "Self-serve chat": { tone: "emerald", icon: MessageSquareText },
  "Already refunded": { tone: "emerald", icon: BadgeCheck },
  Pending: { tone: "amber", icon: Hourglass },
}

export function ResolutionBadge({ label }: { label: string }) {
  if (!label || label === "—") {
    return (
      <Badge variant="outline" className={cn("gap-1.5", TONE.zinc)}>
        <Minus className="size-3 shrink-0" />—
      </Badge>
    )
  }
  const style = METHOD_STYLE[label] ?? { tone: "zinc" as Tone, icon: Minus }
  const Icon = style.icon
  return (
    <Badge variant="outline" className={cn("gap-1.5", TONE[style.tone])}>
      <Icon className="size-3 shrink-0" />
      {label}
    </Badge>
  )
}

export function RefundStatusBadge({ status }: { status: RefundStatus }) {
  const { tone, label } = REFUND_LABEL[status] ?? REFUND_LABEL.unknown
  return (
    <Badge variant="outline" className={cn("gap-1.5", TONE[tone])}>
      <span
        aria-hidden
        className={cn(
          "size-1.5 shrink-0 rounded-full",
          tone === "emerald"
            ? "bg-emerald-500"
            : tone === "amber" || tone === "alert-amber"
              ? "bg-amber-500"
              : tone === "red"
                ? "bg-red-500"
                : "bg-muted-foreground/50",
        )}
      />
      {label}
    </Badge>
  )
}
