/**
 * Small presentational atoms shared by the History table and the run
 * detail sheet: status / refund / outcome badges, stat chips, strategy label.
 */

import { CircleOff, ScrollText, Sparkles } from "lucide-react"
import type { LucideIcon } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"
import type { ChatOutcome, RefundStatus, RunStatus, StrategyName } from "@/lib/types"
import { statNum } from "./run-data"

// ---------------------------------------------------------------------------
// Run status
// ---------------------------------------------------------------------------

const RUN_STATUS: Record<RunStatus, { label: string; className: string }> = {
  running: {
    label: "Running",
    className: "animate-pulse border-primary/30 bg-primary/10 text-primary",
  },
  completed: {
    label: "Completed",
    className:
      "border-emerald-500/25 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
  },
  stopped: {
    label: "Stopped",
    className: "border-zinc-500/25 bg-zinc-500/10 text-zinc-600 dark:text-zinc-400",
  },
  error: {
    label: "Error",
    className: "border-red-500/30 bg-red-500/10 text-red-600 dark:text-red-400",
  },
}

export function RunStatusBadge({ status }: { status: RunStatus }) {
  const def = RUN_STATUS[status] ?? RUN_STATUS.error
  return (
    <Badge variant="outline" className={def.className}>
      {status === "running" ? (
        <span className="size-1.5 rounded-full bg-primary" />
      ) : null}
      {def.label}
    </Badge>
  )
}

// ---------------------------------------------------------------------------
// Refund status (per checked order)
// ---------------------------------------------------------------------------

const REFUND_STATUS: Record<RefundStatus, { label: string; className: string }> = {
  refunded: {
    label: "Refunded",
    className:
      "border-emerald-500/25 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
  },
  partial: {
    label: "Partial",
    className: "border-amber-500/30 bg-amber-500/10 text-amber-600 dark:text-amber-400",
  },
  pending_claim: {
    label: "Self-claim",
    className: "border-sky-500/30 bg-sky-500/10 text-sky-600 dark:text-sky-400",
  },
  not_refunded: {
    label: "Not refunded",
    className: "border-red-500/30 bg-red-500/10 text-red-600 dark:text-red-400",
  },
  remake: {
    label: "Remake",
    className: "border-orange-500/30 bg-orange-500/10 text-orange-600 dark:text-orange-400",
  },
  unconfirmed: {
    label: "⚠ Unconfirmed",
    className: "border-amber-500/40 bg-amber-500/15 font-semibold text-amber-600 dark:text-amber-400",
  },
  unknown: {
    label: "Unknown",
    className: "border-zinc-500/25 bg-zinc-500/10 text-zinc-600 dark:text-zinc-400",
  },
  unchecked: {
    label: "Unchecked",
    className: "border-border bg-transparent text-muted-foreground",
  },
}

export function RefundStatusBadge({ status }: { status: RefundStatus | null }) {
  if (!status) return <span className="text-xs text-muted-foreground/50">—</span>
  const def = REFUND_STATUS[status] ?? REFUND_STATUS.unknown
  return (
    <Badge variant="outline" className={def.className}>
      {def.label}
    </Badge>
  )
}

// ---------------------------------------------------------------------------
// Chat outcome
// ---------------------------------------------------------------------------

const OUTCOME: Record<ChatOutcome, { label: string; className: string }> = {
  success: {
    label: "Refund won",
    className:
      "border-emerald-500/25 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
  },
  failed: {
    label: "Failed",
    className: "border-red-500/30 bg-red-500/10 text-red-600 dark:text-red-400",
  },
  blocked: {
    label: "Blocked",
    className: "border-amber-500/30 bg-amber-500/10 text-amber-600 dark:text-amber-400",
  },
  review_blocked: {
    label: "Review blocked",
    className: "border-amber-500/30 bg-amber-500/10 text-amber-600 dark:text-amber-400",
  },
  manual_flag: {
    label: "Manual follow-up",
    className:
      "border-violet-500/30 bg-violet-500/10 text-violet-600 dark:text-violet-400",
  },
}

export function ChatOutcomeBadge({ outcome }: { outcome: ChatOutcome | null }) {
  if (!outcome) {
    return (
      <Badge
        variant="outline"
        className="border-zinc-500/25 bg-zinc-500/10 text-zinc-600 dark:text-zinc-400"
      >
        No outcome
      </Badge>
    )
  }
  const def = OUTCOME[outcome] ?? { label: outcome, className: REFUND_STATUS.unknown.className }
  return (
    <Badge variant="outline" className={def.className}>
      {def.label}
    </Badge>
  )
}

// ---------------------------------------------------------------------------
// Claim outcome (self-claim, no agent chat)
// ---------------------------------------------------------------------------

const CLAIM_OUTCOME: Record<string, { label: string; className: string }> = {
  success: {
    label: "Claimed to card",
    className:
      "border-emerald-500/25 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
  },
  wrong_method: {
    label: "Wrong method",
    className: "border-amber-500/30 bg-amber-500/10 text-amber-600 dark:text-amber-400",
  },
  failed: {
    label: "Claim failed",
    className: "border-red-500/30 bg-red-500/10 text-red-600 dark:text-red-400",
  },
  error: {
    label: "Claim error",
    className: "border-red-500/30 bg-red-500/10 text-red-600 dark:text-red-400",
  },
}

export function ClaimOutcomeBadge({ outcome }: { outcome: string }) {
  const def = CLAIM_OUTCOME[outcome] ?? {
    label: outcome || "Claim",
    className: REFUND_STATUS.unknown.className,
  }
  return (
    <Badge variant="outline" className={def.className}>
      {def.label}
    </Badge>
  )
}

// ---------------------------------------------------------------------------
// Stats chips ("12 checked · 3 missing · 2 won …")
// ---------------------------------------------------------------------------

const CHIP_DEFS: {
  key: string
  label: string
  className: string
  always?: boolean
}[] = [
  {
    key: "checked",
    label: "checked",
    className: "border-border/60 bg-muted/40 text-muted-foreground",
    always: true,
  },
  {
    key: "not_refunded",
    label: "missing",
    className: "border-red-500/30 bg-red-500/10 text-red-600 dark:text-red-400",
  },
  {
    key: "chats_won",
    label: "won",
    className:
      "border-emerald-500/25 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
  },
  {
    key: "blocked",
    label: "blocked",
    className: "border-amber-500/30 bg-amber-500/10 text-amber-600 dark:text-amber-400",
  },
  {
    key: "manual",
    label: "manual",
    className:
      "border-violet-500/30 bg-violet-500/10 text-violet-600 dark:text-violet-400",
  },
]

export function StatChips({ stats }: { stats: Record<string, unknown> }) {
  // Runs still in flight have an empty stats blob until they finish.
  if (Object.keys(stats).length === 0) {
    return <span className="text-xs text-muted-foreground/50">—</span>
  }
  return (
    <div className="flex flex-wrap items-center gap-1">
      {CHIP_DEFS.map(({ key, label, className, always }) => {
        const n = statNum(stats, key)
        if (n === 0 && !always) return null
        return (
          <span
            key={key}
            className={cn(
              "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium whitespace-nowrap tabular-nums",
              className,
            )}
          >
            {n} {label}
          </span>
        )
      })}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Chat strategy
// ---------------------------------------------------------------------------

const STRATEGY: Record<StrategyName, { label: string; icon: LucideIcon }> = {
  scripted: { label: "Scripted chat", icon: ScrollText },
  llm: { label: "LLM chat", icon: Sparkles },
  none: { label: "Check only", icon: CircleOff },
}

export function StrategyLabel({
  strategy,
  className,
}: {
  strategy: StrategyName
  className?: string
}) {
  const def = STRATEGY[strategy] ?? STRATEGY.none
  const Icon = def.icon
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 whitespace-nowrap text-muted-foreground",
        className,
      )}
    >
      <Icon className="size-3.5 shrink-0" />
      {def.label}
    </span>
  )
}
