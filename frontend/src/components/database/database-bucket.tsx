import { useState } from "react"
import { Link } from "react-router-dom"
import { format, isToday } from "date-fns"
import { ChevronDown, FileText } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader } from "@/components/ui/card"
import { parseBucketDate } from "@/components/customers/helpers"
import type { FullCustomer } from "@/lib/types"
import { cn } from "@/lib/utils"
import { DatabaseCustomer } from "./database-customer"

export function DatabaseBucket({
  date,
  customers,
  defaultOpen = true,
}: {
  date: string
  customers: FullCustomer[]
  defaultOpen?: boolean
}) {
  const [open, setOpen] = useState(defaultOpen)
  const bucketDate = parseBucketDate(date)
  const orderTotal = customers.reduce((n, c) => n + c.orders.length, 0)
  // Day-level roll-up so a COLLAPSED bucket is still scannable.
  let refundedTotal = 0
  let recovered = 0
  let needs = 0
  for (const c of customers) {
    for (const o of c.orders) {
      if (o.refund_status === "refunded") {
        refundedTotal += 1
        recovered += o.refund_amount ?? 0
      } else if (o.refund_status !== "unchecked") {
        needs += 1
      }
    }
  }
  const allDone = orderTotal > 0 && refundedTotal === orderTotal

  return (
    <Card>
      <CardHeader className="border-b">
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
          className="-mx-1 flex items-center gap-3 rounded-lg px-1 text-left"
        >
          <ChevronDown
            className={cn(
              "size-4 shrink-0 text-muted-foreground transition-transform",
              !open && "-rotate-90",
            )}
          />
          <span className="font-heading text-base font-medium">
            {format(bucketDate, "EEE, MMM d yyyy")}
          </span>
          {isToday(bucketDate) ? (
            <Badge className="border-primary/20 bg-primary/10 text-primary">
              Today
            </Badge>
          ) : null}
          <span className="ml-auto flex items-center gap-3 text-xs tabular-nums">
            <span className="text-muted-foreground">
              {customers.length} cust · {orderTotal} ord
            </span>
            {orderTotal > 0 ? (
              <span
                className={cn(
                  "num font-semibold",
                  allDone ? "text-emerald-500" : "text-foreground",
                )}
              >
                {refundedTotal}/{orderTotal} refunded
              </span>
            ) : null}
            {recovered > 0 ? (
              <span className="num text-emerald-500">${recovered.toFixed(2)}</span>
            ) : null}
            {needs > 0 ? (
              <span className="border border-amber-500/40 bg-amber-500/10 px-1.5 py-0.5 font-semibold text-amber-500">
                {needs} open
              </span>
            ) : null}
          </span>
        </button>
      </CardHeader>

      {open ? (
        <CardContent className="space-y-2.5">
          {/* Cross-link to the frozen daily worklog for this date (proof +
              transcripts) — the complement to this live data view. */}
          <Link
            to={`/reports?date=${date}`}
            className="inline-flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground"
          >
            <FileText className="size-3.5" />
            View this day's report
          </Link>
          {customers.map((c) => (
            <DatabaseCustomer key={c.id} customer={c} />
          ))}
        </CardContent>
      ) : null}
    </Card>
  )
}
