import { useState, type ReactNode } from "react"
import { format } from "date-fns"
import {
  ChevronDown,
  Hash,
  Mail,
  MapPin,
  Package,
  Phone,
} from "lucide-react"
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

export function DatabaseCustomer({ customer }: { customer: FullCustomer }) {
  const [open, setOpen] = useState(false)
  const address = addressFromNotes(customer.notes)
  const orderCount = customer.orders.length

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
            <span className="shrink-0 text-xs text-muted-foreground">
              {customer.email || "no email"}
            </span>
          </div>
        </div>
        <CustomerPills pills={customer.pills} className="hidden md:flex" />
        <span className="flex shrink-0 items-center gap-1.5 rounded-md bg-muted/60 px-2 py-1 text-xs text-muted-foreground tabular-nums">
          <Package className="size-3.5" />
          {orderCount} order{orderCount === 1 ? "" : "s"}
        </span>
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
