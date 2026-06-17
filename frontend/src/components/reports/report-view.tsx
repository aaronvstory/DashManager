/**
 * Native, full-width report view — renders /api/reports/<date>/data in the
 * app's brutalist style instead of cramming the standalone HTML into an iframe.
 * Per-customer sections: account line, order table (with How + confidence),
 * proof screenshots (hover-zoom), and chat transcripts.
 */
import { ChatTranscript } from "@/components/history/chat-transcript"
import { RefundStatusBadge, ResolutionBadge } from "@/components/database/order-badges"
import { ProofThumb } from "./proof-thumb"
import type { Chat, Order, ReportCustomer, ReportData } from "@/lib/types"

function money(v: number | null | undefined): string {
  return typeof v === "number" ? `$${v.toFixed(2)}` : "—"
}

function StatBlock({ label, value, tone }: {
  label: string
  value: string | number
  tone?: "good" | "warn" | "alert"
}) {
  const accent =
    tone === "good" ? "text-status-success-fg"
    : tone === "warn" ? "text-status-warning-fg"
    : tone === "alert" ? "text-primary"
    : "text-foreground"
  return (
    <div className="border border-border bg-card px-4 py-3">
      <div className={`num text-2xl font-bold ${accent}`}>{value}</div>
      <div className="eyebrow mt-1">{label}</div>
    </div>
  )
}

function OrderRow({ order }: { order: Order }) {
  const refunded = order.refund_status === "refunded"
  return (
    <tr className="border-t border-border align-top">
      <td className="px-3 py-2.5 font-medium">
        {order.receipt_url ? (
          <a
            href={order.receipt_url}
            target="_blank"
            rel="noreferrer"
            className="hover:text-primary hover:underline"
          >
            {order.store_name || "Order"}
          </a>
        ) : (
          order.store_name || "Order"
        )}
      </td>
      <td className="px-3 py-2.5">
        <RefundStatusBadge status={order.refund_status} />
      </td>
      <td className="px-3 py-2.5">
        <ResolutionBadge label={order.resolution?.label ?? "—"} />
      </td>
      <td className="num px-3 py-2.5 text-right">{money(order.total_amount ?? order.price)}</td>
      <td className={`num px-3 py-2.5 text-right ${refunded ? "text-status-success-fg" : "text-muted-foreground"}`}>
        {order.refund_amount ? money(order.refund_amount) : "—"}
      </td>
    </tr>
  )
}

function CustomerSection({ customer }: { customer: ReportCustomer }) {
  const name = `${customer.first_name} ${customer.last_name}`.trim() || `Customer ${customer.id}`
  const orders = customer.orders ?? []
  const refundedTotal = orders
    .filter((o) => o.refund_status === "refunded")
    .reduce((s, o) => s + (o.refund_amount ?? 0), 0)
  // gather all proof shots: customer-level + per-order
  const shots = [
    ...(customer.screenshots ?? []),
    ...orders.flatMap((o) => o.screenshots ?? []),
  ]
  const chats: Chat[] = orders.flatMap((o) => o.chats ?? [])

  return (
    <section className="border border-border bg-card">
      {/* header bar */}
      <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1 border-b border-border bg-muted/30 px-4 py-3">
        <div className="flex items-baseline gap-3">
          <h3 className="text-lg font-bold tracking-tight">{name}</h3>
          <span className="text-sm text-muted-foreground">{customer.email}</span>
        </div>
        <div className="flex items-center gap-4 text-sm">
          <span className="text-muted-foreground">
            {orders.length} order{orders.length === 1 ? "" : "s"}
          </span>
          <span className="num font-bold text-status-success-fg">{money(refundedTotal)}</span>
        </div>
      </div>

      <div className="px-4 py-3">
        {orders.length === 0 ? (
          <p className="py-2 text-sm text-muted-foreground">No orders captured.</p>
        ) : (
          <table className="w-full border border-border text-sm">
            <thead>
              <tr className="bg-muted/40">
                <th className="eyebrow px-3 py-2 text-left">Store</th>
                <th className="eyebrow px-3 py-2 text-left">Refund</th>
                <th className="eyebrow px-3 py-2 text-left">How</th>
                <th className="eyebrow px-3 py-2 text-right">Total</th>
                <th className="eyebrow px-3 py-2 text-right">Refunded</th>
              </tr>
            </thead>
            <tbody>
              {orders.map((o) => (
                <OrderRow key={o.id} order={o} />
              ))}
            </tbody>
          </table>
        )}

        {/* proof screenshots — hover to zoom */}
        {shots.length > 0 ? (
          <div className="mt-4">
            <div className="eyebrow mb-2">Proof · hover to zoom</div>
            <div className="flex flex-wrap gap-2">
              {shots.map((s, i) => (
                <ProofThumb key={`${s.url}-${i}`} shot={s} />
              ))}
            </div>
          </div>
        ) : null}

        {/* chat transcripts */}
        {chats.map((chat) => (
          <div key={chat.id} className="mt-4 border border-border">
            <div className="eyebrow flex items-center gap-2 border-b border-border bg-muted/30 px-3 py-2">
              Support chat
              {chat.agent_reached ? (
                <span className="text-status-success-fg">· agent reached</span>
              ) : null}
              {chat.outcome ? <span className="ml-auto normal-case">{chat.outcome}</span> : null}
            </div>
            <ChatTranscript messages={chat.messages ?? []} />
          </div>
        ))}
      </div>
    </section>
  )
}

export function ReportView({ data }: { data: ReportData }) {
  const s = data.summary
  return (
    <div className="space-y-5">
      {/* summary band */}
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
        <StatBlock label="Customers" value={s.customers} />
        <StatBlock label="Orders" value={s.orders} />
        <StatBlock label="Refunded" value={s.refunded} tone="good" />
        <StatBlock label="Pursuing" value={s.pursuing} tone={s.pursuing ? "warn" : undefined} />
        <StatBlock label="Unconfirmed" value={s.unconfirmed} tone={s.unconfirmed ? "alert" : undefined} />
        <StatBlock label="Recovered" value={money(s.total_refunded)} tone="good" />
      </div>

      {/* per-customer sections */}
      <div className="space-y-4">
        {data.customers.map((c) => (
          <CustomerSection key={c.id} customer={c} />
        ))}
      </div>
    </div>
  )
}
