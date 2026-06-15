import { useState, type ReactNode } from "react"
import { format } from "date-fns"
import { ChevronDown, Hash, Mail, MapPin, Phone } from "lucide-react"
import { CustomerPills } from "@/components/customers/customer-pills"
import {
  customerName,
  hasRealName,
  parseDbTimestamp,
} from "@/components/customers/helpers"
import type { FullCustomer } from "@/lib/types"
import { cn } from "@/lib/utils"
import { CopyToken } from "./copy-token"
import { OrdersTable } from "./orders-table"

/** Pull a human address out of the notes blob, falling back to nothing. */
function addressFromNotes(notes: string): string {
  if (!notes) return ""
  // Notes may carry an "Address: ..." line; otherwise show the whole blob.
  const match = notes.match(/address\s*[:=]\s*(.+)/i)
  return (match?.[1] ?? notes).trim()
}

function Detail({
  icon: Icon,
  children,
}: {
  icon: typeof Mail
  children: ReactNode
}) {
  return (
    <div className="flex items-center gap-2 text-muted-foreground">
      <Icon className="size-3.5 shrink-0 text-muted-foreground/70" />
      <span className="min-w-0 truncate text-foreground/90">{children}</span>
    </div>
  )
}

/** Compact per-customer roll-up shown even when the row is collapsed:
 *  refunded count, $ recovered, and a flag if anything still needs attention. */
function orderRollup(customer: FullCustomer) {
  const orders = customer.orders
  let refunded = 0
  let recovered = 0
  let needs = 0
  let unconfirmed = 0
  for (const o of orders) {
    const st = o.refund_status
    if (st === "refunded") {
      refunded += 1
      recovered += o.refund_amount ?? 0
    } else if (st === "unconfirmed") {
      unconfirmed += 1
      needs += 1
    } else if (st !== "unchecked") {
      needs += 1
    }
  }
  return { count: orders.length, refunded, recovered, needs, unconfirmed }
}

function money(v: number): string {
  return `$${v.toFixed(2)}`
}

export function DatabaseCustomer({ customer }: { customer: FullCustomer }) {
  const [open, setOpen] = useState(false)
  const address = addressFromNotes(customer.notes)
  const r = orderRollup(customer)
  const allRefunded = r.count > 0 && r.refunded === r.count

  return (
    <div className="overflow-hidden border border-border bg-card">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex w-full items-center gap-4 px-4 py-3 text-left transition-colors hover:bg-muted/40"
      >
        <ChevronDown
          className={cn(
            "size-4 shrink-0 text-muted-foreground transition-transform",
            open && "rotate-180",
          )}
        />
        {/* status dot — instant scan: green = all refunded, amber = needs you */}
        <span
          aria-hidden
          className={cn(
            "size-2 shrink-0",
            r.count === 0
              ? "bg-muted-foreground/40"
              : allRefunded
                ? "bg-emerald-500"
                : r.needs > 0
                  ? "bg-amber-500"
                  : "bg-muted-foreground/40",
          )}
        />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span
              className={cn(
                "truncate font-medium",
                !hasRealName(customer) &&
                  "font-normal text-muted-foreground italic",
              )}
            >
              {customerName(customer)}
            </span>
            <span className="hidden shrink-0 text-xs text-muted-foreground sm:inline">
              {customer.email || "no email"}
            </span>
          </div>
        </div>

        {/* COLLAPSED-STATE SCANNABLE SUMMARY: refunded count · $ · flags */}
        <div className="flex shrink-0 items-center gap-2 text-xs tabular-nums">
          {r.count === 0 ? (
            <span className="text-muted-foreground">0 orders</span>
          ) : (
            <>
              <span
                className={cn(
                  "num font-semibold",
                  allRefunded ? "text-emerald-500" : "text-foreground",
                )}
              >
                {r.refunded}/{r.count} refunded
              </span>
              {r.recovered > 0 ? (
                <span className="num text-emerald-500">{money(r.recovered)}</span>
              ) : null}
              {r.unconfirmed > 0 ? (
                <span className="border border-amber-500/40 bg-amber-500/15 px-1.5 py-0.5 font-semibold text-amber-500">
                  ⚠ {r.unconfirmed}
                </span>
              ) : r.needs > 0 ? (
                <span className="border border-amber-500/40 bg-amber-500/10 px-1.5 py-0.5 text-amber-500">
                  {r.needs} open
                </span>
              ) : null}
            </>
          )}
        </div>
        <CustomerPills pills={customer.pills} className="hidden shrink-0 md:flex" />
      </button>

      {open ? (
        <div className="space-y-4 border-t border-border/60 bg-background/40 px-4 py-4">
          <div className="md:hidden">
            <CustomerPills pills={customer.pills} />
          </div>

          <div className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2 lg:grid-cols-3">
            {customer.email ? (
              <Detail icon={Mail}>{customer.email}</Detail>
            ) : null}
            {customer.phone ? (
              <Detail icon={Phone}>{customer.phone}</Detail>
            ) : null}
            {address ? <Detail icon={MapPin}>{address}</Detail> : null}
            <div className="flex items-center gap-2 text-muted-foreground">
              <Hash className="size-3.5 shrink-0 text-muted-foreground/70" />
              {customer.number_token ? (
                <CopyToken
                  value={customer.number_token}
                  label="number token"
                />
              ) : (
                <span className="text-muted-foreground/70">No number token</span>
              )}
            </div>
            <div className="text-muted-foreground">
              Added{" "}
              <span className="text-foreground/90">
                {format(parseDbTimestamp(customer.created_at), "MMM d, yyyy")}
              </span>
            </div>
          </div>

          <OrdersTable orders={customer.orders} />
        </div>
      ) : null}
    </div>
  )
}
