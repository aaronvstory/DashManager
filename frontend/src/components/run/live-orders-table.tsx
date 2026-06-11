import { ExternalLink, PackageSearch } from "lucide-react"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { ScrollArea } from "@/components/ui/scroll-area"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { OrderStatusBadge, RefundStatusBadge } from "@/components/run/badges"
import { useRunStore } from "@/store/runStore"
import { cn } from "@/lib/utils"

function money(v: number | null | undefined): string {
  return typeof v === "number" ? `$${v.toFixed(2)}` : "—"
}

/**
 * Orders stream in via order_checking/order_checked. Newest first so the
 * order currently being checked is always visible at the top.
 */
export function LiveOrdersTable() {
  const orders = useRunStore((s) => s.orders)
  const list = Object.values(orders).sort((a, b) => b.order_id - a.order_id)

  return (
    <Card className="gap-3">
      <CardHeader>
        <CardTitle>Orders</CardTitle>
        <CardDescription>
          {list.length === 0
            ? "Receipts will appear here as they are checked."
            : `${list.length} receipt${list.length === 1 ? "" : "s"} this run`}
        </CardDescription>
      </CardHeader>
      <CardContent className="px-0">
        {list.length === 0 ? (
          <div className="flex flex-col items-center gap-2 px-4 py-10 text-center">
            <PackageSearch className="size-6 text-muted-foreground/60" />
            <p className="text-sm text-muted-foreground">
              Waiting for the first receipt…
            </p>
          </div>
        ) : (
          <ScrollArea className="max-h-80">
            <Table>
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead className="pl-4">Store</TableHead>
                  <TableHead>Refund status</TableHead>
                  <TableHead className="text-right">Refunded</TableHead>
                  <TableHead className="text-right">Total</TableHead>
                  <TableHead className="w-10 pr-4" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {list.map((o) => (
                  <TableRow
                    key={o.order_id}
                    className={cn(o.checking && "bg-primary/[0.03]")}
                  >
                    <TableCell className="pl-4">
                      <div className="flex items-center gap-2">
                        <span className="max-w-56 truncate font-medium">
                          {o.store || `Order #${o.order_id}`}
                        </span>
                        {o.order_status === "cancelled" ? (
                          <OrderStatusBadge status="cancelled" />
                        ) : null}
                      </div>
                      {typeof o.items_count === "number" ||
                      typeof o.price === "number" ? (
                        <div className="text-xs text-muted-foreground">
                          {[
                            typeof o.items_count === "number"
                              ? `${o.items_count} item${o.items_count === 1 ? "" : "s"}`
                              : null,
                            typeof o.price === "number" ? money(o.price) : null,
                          ]
                            .filter(Boolean)
                            .join(" · ")}
                        </div>
                      ) : null}
                    </TableCell>
                    <TableCell>
                      <RefundStatusBadge
                        status={o.refund_status}
                        checking={o.checking}
                      />
                    </TableCell>
                    <TableCell
                      className={cn(
                        "text-right tabular-nums",
                        typeof o.refund_amount === "number" && o.refund_amount > 0
                          ? "font-medium text-emerald-600 dark:text-emerald-400"
                          : "text-muted-foreground",
                      )}
                    >
                      {money(o.refund_amount)}
                    </TableCell>
                    <TableCell className="text-right text-muted-foreground tabular-nums">
                      {money(o.total_amount)}
                    </TableCell>
                    <TableCell className="pr-4 text-right">
                      {o.url ? (
                        <a
                          href={o.url}
                          target="_blank"
                          rel="noreferrer"
                          className="inline-flex text-muted-foreground transition-colors hover:text-foreground"
                          title="Open receipt"
                        >
                          <ExternalLink className="size-3.5" />
                        </a>
                      ) : null}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </ScrollArea>
        )}
      </CardContent>
    </Card>
  )
}
