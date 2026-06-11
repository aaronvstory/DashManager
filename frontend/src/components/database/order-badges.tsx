import { Truck } from "lucide-react"
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
  not_refunded: { tone: "red", label: "Not refunded" },
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
            : tone === "amber"
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
