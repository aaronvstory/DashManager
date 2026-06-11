import { ExternalLink, Package } from "lucide-react"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import type { Order } from "@/lib/types"
import { parseDbTimestamp } from "@/components/customers/helpers"
import { OrderStatusBadge, RefundStatusBadge } from "./order-badges"

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

export function OrdersTable({ orders }: { orders: Order[] }) {
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
            <TableHead className="pl-3">Store</TableHead>
            <TableHead>Description</TableHead>
            <TableHead className="text-right">Items</TableHead>
            <TableHead className="text-right">Price</TableHead>
            <TableHead>Order status</TableHead>
            <TableHead>Refund</TableHead>
            <TableHead className="text-right">Total</TableHead>
            <TableHead className="text-right">Refunded</TableHead>
            <TableHead className="pr-3 text-right">Checked</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {orders.map((o) => (
            <TableRow key={o.id}>
              <TableCell className="pl-3 font-medium">
                {o.receipt_url ? (
                  <a
                    href={o.receipt_url}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-1 hover:text-primary"
                  >
                    {o.store_name || "Order"}
                    <ExternalLink className="size-3 text-muted-foreground" />
                  </a>
                ) : (
                  o.store_name || "Order"
                )}
              </TableCell>
              <TableCell className="max-w-56 truncate text-muted-foreground">
                {o.description || "—"}
              </TableCell>
              <TableCell className="text-right text-muted-foreground tabular-nums">
                {o.items_count ?? "—"}
              </TableCell>
              <TableCell className="text-right tabular-nums">
                {money(o.price)}
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
              <TableCell className="text-right tabular-nums">
                {money(o.total_amount)}
              </TableCell>
              <TableCell className="text-right tabular-nums text-emerald-600 dark:text-emerald-400">
                {o.refund_amount ? money(o.refund_amount) : "—"}
              </TableCell>
              <TableCell className="pr-3 text-right text-xs text-muted-foreground">
                {checkedAgo(o.last_checked_at)}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}
