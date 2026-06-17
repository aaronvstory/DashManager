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
import { DOT, TONE, type Tone } from "@/lib/status-tone"
import { cn } from "@/lib/utils"

/**
 * Order + refund status badges for the Database viewer. Same outline + tinted
 * ring language as the customer pills, so the page reads as one family.
 */

const ORDER_LABEL: Record<OrderLifecycle, { tone: Tone; label: string }> = {
  in_progress: { tone: "info", label: "In progress" },
  active: { tone: "info", label: "In progress" },
  completed: { tone: "neutral", label: "Completed" },
  cancelled: { tone: "critical", label: "Cancelled" },
}

const REFUND_LABEL: Record<RefundStatus, { tone: Tone; label: string }> = {
  refunded: { tone: "success", label: "Refunded" },
  partial: { tone: "warning", label: "Partial refund" },
  pending_claim: { tone: "info", label: "Self-claim" },
  not_refunded: { tone: "critical", label: "Not refunded" },
  remake: { tone: "warning", label: "Remake" },
  // ZERO-TOLERANCE: an action ran but we could NOT prove it landed on the
  // card. Loud warning — must read as "needs a human", never as done.
  unconfirmed: { tone: "warning-strong", label: "⚠ Unconfirmed" },
  unchecked: { tone: "neutral", label: "Unchecked" },
  unknown: { tone: "warning", label: "Unknown" },
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
              tone === "critical" ? DOT.critical : DOT.neutral,
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
  "Self-claim": { tone: "info", icon: Hand },
  "Agent chat": { tone: "success", icon: MessageSquareText },
  "Credits→card (agent chat)": { tone: "success", icon: CreditCard },
  "Self-serve chat": { tone: "success", icon: MessageSquareText },
  "Already refunded": { tone: "success", icon: BadgeCheck },
  Pending: { tone: "warning", icon: Hourglass },
}

export function ResolutionBadge({ label }: { label: string }) {
  if (!label || label === "—") {
    return (
      <Badge variant="outline" className={cn("gap-1.5", TONE.neutral)}>
        <Minus className="size-3 shrink-0" />—
      </Badge>
    )
  }
  const style = METHOD_STYLE[label] ?? { tone: "neutral" as Tone, icon: Minus }
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
        className={cn("size-1.5 shrink-0 rounded-full", DOT[tone])}
      />
      {label}
    </Badge>
  )
}
