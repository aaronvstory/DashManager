import { Bot, Loader2, ScrollText, SearchCheck } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import type {
  ChatOutcome,
  OrderStatus,
  RefundStatus,
  StrategyName,
} from "@/lib/types"
import { cn } from "@/lib/utils"

// ---------------------------------------------------------------------------
// Refund status — emerald / amber / red / zinc per the design spec
// ---------------------------------------------------------------------------

const REFUND_STYLES: Record<RefundStatus, { label: string; className: string }> = {
  refunded: {
    label: "Refunded",
    className:
      "border-emerald-500/25 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
  },
  partial: {
    label: "Partial",
    className:
      "border-amber-500/25 bg-amber-500/10 text-amber-600 dark:text-amber-400",
  },
  not_refunded: {
    label: "Not refunded",
    className: "border-red-500/25 bg-red-500/10 text-red-600 dark:text-red-400",
  },
  unknown: {
    label: "Unknown",
    className: "border-border bg-muted text-muted-foreground",
  },
  unchecked: {
    label: "Unchecked",
    className: "border-border bg-transparent text-muted-foreground",
  },
}

export function RefundStatusBadge({
  status,
  checking,
  className,
}: {
  status?: RefundStatus
  checking?: boolean
  className?: string
}) {
  if (checking) {
    return (
      <Badge
        variant="outline"
        className={cn("gap-1 border-primary/25 bg-primary/5 text-primary", className)}
      >
        <Loader2 className="size-3 animate-spin" />
        Checking…
      </Badge>
    )
  }
  const s = REFUND_STYLES[status ?? "unchecked"]
  return (
    <Badge variant="outline" className={cn(s.className, className)}>
      {s.label}
    </Badge>
  )
}

// ---------------------------------------------------------------------------
// Order status — cancelled gets an outline red tint
// ---------------------------------------------------------------------------

export function OrderStatusBadge({
  status,
  className,
}: {
  status: OrderStatus
  className?: string
}) {
  if (status === "cancelled") {
    return (
      <Badge
        variant="outline"
        className={cn("border-red-500/30 bg-red-500/5 text-red-600 dark:text-red-400", className)}
      >
        Cancelled
      </Badge>
    )
  }
  return (
    <Badge variant="outline" className={cn("text-muted-foreground", className)}>
      Active
    </Badge>
  )
}

// ---------------------------------------------------------------------------
// Chat outcome
// ---------------------------------------------------------------------------

const OUTCOME_STYLES: Record<ChatOutcome, { label: string; className: string }> = {
  success: {
    label: "Refund won",
    className:
      "border-emerald-500/25 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
  },
  failed: {
    label: "Failed",
    className: "border-red-500/25 bg-red-500/10 text-red-600 dark:text-red-400",
  },
  blocked: {
    label: "Blocked",
    className:
      "border-amber-500/25 bg-amber-500/10 text-amber-600 dark:text-amber-400",
  },
  review_blocked: {
    label: "Review blocked",
    className:
      "border-amber-500/25 bg-amber-500/10 text-amber-600 dark:text-amber-400",
  },
  manual_flag: {
    label: "Needs human",
    className: "border-sky-500/25 bg-sky-500/10 text-sky-600 dark:text-sky-400",
  },
}

export function OutcomeBadge({
  outcome,
  className,
}: {
  outcome: ChatOutcome
  className?: string
}) {
  const s = OUTCOME_STYLES[outcome]
  return (
    <Badge variant="outline" className={cn(s.className, className)}>
      {s.label}
    </Badge>
  )
}

/** Pulsing "still chatting" indicator shown until chat_outcome arrives. */
export function InChatBadge({ className }: { className?: string }) {
  return (
    <Badge
      variant="outline"
      className={cn("gap-1.5 border-primary/25 bg-primary/5 text-primary", className)}
    >
      <span className="relative flex size-1.5">
        <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-primary/70" />
        <span className="relative inline-flex size-1.5 rounded-full bg-primary" />
      </span>
      In chat…
    </Badge>
  )
}

// ---------------------------------------------------------------------------
// Chat strategy
// ---------------------------------------------------------------------------

const STRATEGY_META: Record<
  StrategyName,
  { label: string; icon: typeof Bot; className: string }
> = {
  scripted: {
    label: "Scripted chat",
    icon: ScrollText,
    className: "border-border bg-secondary text-secondary-foreground",
  },
  llm: {
    label: "LLM chat",
    icon: Bot,
    className: "border-primary/25 bg-primary/10 text-primary",
  },
  none: {
    label: "Detect only",
    icon: SearchCheck,
    className: "border-border bg-transparent text-muted-foreground",
  },
}

export function StrategyBadge({
  strategy,
  className,
}: {
  strategy: StrategyName
  className?: string
}) {
  const meta = STRATEGY_META[strategy]
  const Icon = meta.icon
  return (
    <Badge variant="outline" className={cn("gap-1", meta.className, className)}>
      <Icon className="size-3" />
      {meta.label}
    </Badge>
  )
}
