import { useState } from "react"
import { format, isToday } from "date-fns"
import { ChevronDown } from "lucide-react"
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
          <span className="ml-auto text-xs text-muted-foreground tabular-nums">
            {customers.length} customer{customers.length === 1 ? "" : "s"} ·{" "}
            {orderTotal} order{orderTotal === 1 ? "" : "s"}
          </span>
        </button>
      </CardHeader>

      {open ? (
        <CardContent className="space-y-2.5">
          {customers.map((c) => (
            <DatabaseCustomer key={c.id} customer={c} />
          ))}
        </CardContent>
      ) : null}
    </Card>
  )
}
