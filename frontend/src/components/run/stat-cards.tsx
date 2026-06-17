import { MessageSquareHeart, ReceiptText, SearchX, ShieldAlert } from "lucide-react"
import type { LucideIcon } from "lucide-react"
import { Card } from "@/components/ui/card"
import { useRunStore } from "@/store/runStore"
import { cn } from "@/lib/utils"

interface StatDef {
  key: string
  label: string
  icon: LucideIcon
  accent: string
  iconBg: string
}

const STATS: StatDef[] = [
  {
    key: "checked",
    label: "Checked",
    icon: ReceiptText,
    accent: "text-foreground",
    iconBg: "bg-muted text-muted-foreground",
  },
  {
    key: "not_refunded",
    label: "Missing refund",
    icon: SearchX,
    accent: "text-status-critical-fg",
    iconBg: "bg-status-critical/10 text-status-critical-fg",
  },
  {
    key: "chats_won",
    label: "Chats won",
    icon: MessageSquareHeart,
    accent: "text-status-success-fg",
    iconBg: "bg-status-success/10 text-status-success-fg",
  },
  {
    key: "blocked",
    label: "Blocked",
    icon: ShieldAlert,
    accent: "text-status-warning-fg",
    iconBg: "bg-status-warning/10 text-status-warning-fg",
  },
]

/**
 * Four headline counters. `stats` snapshots only land on customer_done, so we
 * also derive live values from the orders/chats maps and show whichever is
 * fresher (counts only ever grow within a run).
 */
export function StatCards() {
  const stats = useRunStore((s) => s.stats)
  const orders = useRunStore((s) => s.orders)
  const chats = useRunStore((s) => s.chats)

  const orderList = Object.values(orders)
  const chatList = Object.values(chats)

  const live: Record<string, number> = {
    checked: orderList.filter((o) => !o.checking && o.refund_status !== undefined)
      .length,
    not_refunded: orderList.filter(
      (o) => o.refund_status === "not_refunded" || o.refund_status === "partial",
    ).length,
    chats_won: chatList.filter((c) => c.outcome === "success").length,
    blocked: chatList.filter(
      (c) => c.outcome === "blocked" || c.outcome === "review_blocked",
    ).length,
  }

  return (
    <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
      {STATS.map(({ key, label, icon: Icon, accent, iconBg }) => {
        const value = Math.max(stats[key] ?? 0, live[key] ?? 0)
        return (
          <Card key={key} size="sm" className="flex-row items-center gap-3 px-4">
            <div
              className={cn(
                "flex size-9 shrink-0 items-center justify-center rounded-lg",
                iconBg,
              )}
            >
              <Icon className="size-4" />
            </div>
            <div className="min-w-0">
              <div className={cn("text-xl font-semibold tabular-nums", accent)}>
                {value}
              </div>
              <div className="truncate text-xs text-muted-foreground">{label}</div>
            </div>
          </Card>
        )
      })}
    </div>
  )
}
