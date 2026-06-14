import { Fragment, useState } from "react"
import { ChevronRight, ExternalLink, MessageSquareText, Package } from "lucide-react"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { ChatTranscript } from "@/components/history/chat-transcript"
import type { Order } from "@/lib/types"
import { parseDbTimestamp } from "@/components/customers/helpers"
import { cn } from "@/lib/utils"
import { OrderStatusBadge, RefundStatusBadge, ResolutionBadge } from "./order-badges"

function money(value: number | null | undefined): string {
  return typeof value === "number" ? `$${value.toFixed(2)}` : "—"
}

function checkedAgo(raw: string | null): string {
  if (!raw) return "Never"
  try {
    const then = parseDbTimestamp(raw)
    const mins = Math.floor((Date.now() - then.getTime()) / 60_000)
    if (mins < 1) return "Just now"
    if (mins < 60) return `${mins}m ago`
    const hrs = Math.floor(mins / 60)
    if (hrs < 24) return `${hrs}h ago`
    return `${Math.floor(hrs / 24)}d ago`
  } catch {
    return "—"
  }
}

/** An order has an audit trail worth expanding when it has a chat or a claim. */
function hasTrail(o: Order): boolean {
  return (o.chats?.length ?? 0) > 0 || (o.claims?.length ?? 0) > 0
}

const COLSPAN = 7

function OrderTrail({ order }: { order: Order }) {
  const chats = order.chats ?? []
  const claims = order.claims ?? []
  return (
    <div className="space-y-4 px-4 py-4">
      {order.resolution?.confirmation ? (
        <div className="flex items-start gap-2.5 rounded-lg border border-emerald-500/20 bg-emerald-500/[0.06] px-3.5 py-2.5">
          <ResolutionBadge label={order.resolution.label} />
          <p className="min-w-0 flex-1 text-sm leading-relaxed text-foreground/80">
            {order.resolution.confirmation}
          </p>
        </div>
      ) : null}

      {claims.map((c) => (
        <div
          key={`claim-${c.id}`}
          className="rounded-lg border border-border/60 bg-card/40 px-3.5 py-2.5 text-sm"
        >
          <div className="flex items-center gap-2 font-medium">
            <span className="text-muted-foreground">Self-claim</span>
            <span
              className={cn(
                "rounded px-1.5 py-0.5 text-xs",
                c.confirmed
                  ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
                  : "bg-amber-500/10 text-amber-600 dark:text-amber-400",
              )}
            >
              {c.confirmed ? "confirmed" : c.outcome}
            </span>
          </div>
          <p className="mt-1 text-muted-foreground">
            {money(c.amount)}{" "}
            {c.to_original_payment ? "to original card" : "(method unclear)"}
          </p>
        </div>
      ))}

      {chats.map((chat) => (
        <div
          key={`chat-${chat.id}`}
          className="overflow-hidden border border-border bg-card"
        >
          <div className="flex items-center gap-2 border-b border-border px-3.5 py-2 text-xs text-muted-foreground">
            <MessageSquareText className="size-3.5" />
            Support chat
            {chat.agent_reached ? (
              <span className="rounded bg-emerald-500/10 px-1.5 py-0.5 text-emerald-600 dark:text-emerald-400">
                agent reached
              </span>
            ) : null}
            {chat.outcome ? (
              <span className="ml-auto font-medium">{chat.outcome}</span>
            ) : null}
          </div>
          <ChatTranscript messages={chat.messages ?? []} />
        </div>
      ))}
    </div>
  )
}

export function OrdersTable({ orders }: { orders: Order[] }) {
  const [openId, setOpenId] = useState<number | null>(null)

  if (orders.length === 0) {
    return (
      <div className="flex items-center gap-2 rounded-lg border border-dashed border-border/60 bg-muted/20 px-3 py-4 text-xs text-muted-foreground">
        <Package className="size-4" />
        No orders captured for this customer yet.
      </div>
    )
  }

  return (
    <div className="overflow-hidden rounded-lg border border-border/60">
      <Table>
        <TableHeader>
          <TableRow className="bg-muted/40 hover:bg-muted/40">
            <TableHead className="w-8 pl-3" />
            <TableHead>Store</TableHead>
            <TableHead>Order status</TableHead>
            <TableHead>Refund</TableHead>
            <TableHead>How</TableHead>
            <TableHead className="text-right">Total</TableHead>
            <TableHead className="pr-3 text-right">Refunded</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {orders.map((o) => {
            const expandable = hasTrail(o)
            const open = openId === o.id
            return (
              <Fragment key={o.id}>
                <TableRow
                  className={cn(
                    expandable && "cursor-pointer",
                    open && "bg-muted/30",
                  )}
                  onClick={
                    expandable ? () => setOpenId(open ? null : o.id) : undefined
                  }
                >
                  <TableCell className="pl-3">
                    {expandable ? (
                      <ChevronRight
                        className={cn(
                          "size-4 text-muted-foreground transition-transform",
                          open && "rotate-90",
                        )}
                      />
                    ) : null}
                  </TableCell>
                  <TableCell className="font-medium">
                    {o.receipt_url ? (
                      <a
                        href={o.receipt_url}
                        target="_blank"
                        rel="noreferrer"
                        onClick={(e) => e.stopPropagation()}
                        className="inline-flex items-center gap-1 hover:text-primary"
                      >
                        {o.store_name || "Order"}
                        <ExternalLink className="size-3 text-muted-foreground" />
                      </a>
                    ) : (
                      o.store_name || "Order"
                    )}
                    <span className="block text-xs text-muted-foreground">
                      {checkedAgo(o.last_checked_at)}
                    </span>
                  </TableCell>
                  <TableCell>
                    <OrderStatusBadge
                      status={o.order_status}
                      statusText={o.status_text}
                      dasherName={o.dasher_name}
                    />
                  </TableCell>
                  <TableCell>
                    <RefundStatusBadge status={o.refund_status} />
                  </TableCell>
                  <TableCell>
                    <ResolutionBadge label={o.resolution?.label ?? "—"} />
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {money(o.total_amount ?? o.price)}
                  </TableCell>
                  <TableCell className="pr-3 text-right tabular-nums text-emerald-600 dark:text-emerald-400">
                    {o.refund_amount ? money(o.refund_amount) : "—"}
                  </TableCell>
                </TableRow>
                {open ? (
                  <TableRow className="hover:bg-transparent">
                    <TableCell colSpan={COLSPAN} className="bg-muted/20 p-0">
                      <OrderTrail order={o} />
                    </TableCell>
                  </TableRow>
                ) : null}
              </Fragment>
            )
          })}
        </TableBody>
      </Table>
    </div>
  )
}
