/**
 * Slide-over audit view for a single run: headline numbers (missing refunds /
 * chats / how it ended), every checked order, and full chat transcripts.
 *
 * Detail is fetched lazily when the sheet opens; while the run is still
 * running we poll so the audit trail fills in live.
 */

import { useState } from "react"
import type { ReactNode } from "react"
import { useQuery } from "@tanstack/react-query"
import { format } from "date-fns"
import {
  AlertCircle,
  BadgeCheck,
  Camera,
  MessagesSquare,
  ShoppingBag,
} from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { api } from "@/lib/api"
import type { Claim, Run } from "@/lib/types"
import { cn } from "@/lib/utils"
import {
  ChatOutcomeBadge,
  ClaimOutcomeBadge,
  RefundStatusBadge,
  RunStatusBadge,
  StrategyLabel,
} from "./badges"
import { ChatTranscript } from "./chat-transcript"
import {
  basename,
  groupByOrder,
  money,
  parseDbDate,
  runDuration,
  scopeSummary,
  statNum,
} from "./run-data"
import type {
  ChatWithMessages,
  OrderAudit,
  RunDetailResponse,
} from "./run-data"

function HeadlineStat({
  label,
  value,
  sub,
}: {
  label: string
  value: ReactNode
  sub?: string
}) {
  return (
    <div className="flex flex-col gap-1 px-4 py-3">
      <span className="text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
        {label}
      </span>
      <span className="text-xl font-semibold tabular-nums">{value}</span>
      {sub ? <span className="text-xs text-muted-foreground">{sub}</span> : null}
    </div>
  )
}

function SectionHeading({
  icon: Icon,
  title,
  count,
}: {
  icon: typeof ShoppingBag
  title: string
  count: number
}) {
  return (
    <h3 className="mb-2 flex items-center gap-2 text-sm font-medium">
      <Icon className="size-4 text-muted-foreground" />
      {title}
      <span className="font-normal text-muted-foreground tabular-nums">({count})</span>
    </h3>
  )
}

/** One chat attempt card (header + transcript bubbles). */
function ChatCard({ chat }: { chat: ChatWithMessages }) {
  return (
    <Card size="sm" className="gap-0 overflow-hidden py-0">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b bg-muted/30 px-3 py-2">
        <span className="text-xs font-medium">Attempt {chat.attempt_no}</span>
        <span className="text-xs text-muted-foreground">Chat #{chat.id}</span>
        <div className="ml-auto flex items-center gap-2.5">
          {chat.agent_reached ? (
            <span className="inline-flex items-center gap-1 text-[11px] font-medium text-status-success-fg">
              <span className="size-1.5 rounded-full bg-status-success" />
              Agent reached
            </span>
          ) : (
            <span className="inline-flex items-center gap-1 text-[11px] text-muted-foreground">
              <span className="size-1.5 rounded-full bg-muted-foreground/50" />
              No agent
            </span>
          )}
          <ChatOutcomeBadge outcome={chat.outcome} />
        </div>
      </div>
      <ChatTranscript messages={chat.messages} />
    </Card>
  )
}

/** A self-claim record row (refund resolved without a chat). */
function ClaimRow({ claim }: { claim: Claim }) {
  const amount = money(claim.amount ?? null)
  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1 rounded-lg border bg-muted/20 px-3 py-2 text-xs">
      <BadgeCheck
        className={cn(
          "size-4 shrink-0",
          claim.outcome === "success"
            ? "text-status-success-fg"
            : "text-muted-foreground",
        )}
      />
      <span className="font-medium">Self-claim</span>
      {amount ? <span className="tabular-nums">{amount}</span> : null}
      {claim.to_original_payment ? (
        <span className="text-muted-foreground">→ original card</span>
      ) : claim.outcome === "success" ? (
        <span className="text-muted-foreground">refund posted</span>
      ) : null}
      <div className="ml-auto flex items-center gap-2">
        {claim.error ? (
          <span
            title={claim.error}
            className="max-w-36 truncate text-status-critical-fg"
          >
            {claim.error}
          </span>
        ) : null}
        <ClaimOutcomeBadge outcome={claim.outcome} />
      </div>
    </div>
  )
}

/** Per-order audit block: the order header + its claims + stacked chats. */
function OrderAuditCard({ audit }: { audit: OrderAudit }) {
  const { order, chats, claims } = audit
  return (
    <div className="space-y-2 rounded-xl border bg-card/40 p-3">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className="max-w-52 truncate text-sm font-medium">
          {order.store_name || "Unknown store"}
        </span>
        {money(order.price) ? (
          <span className="text-xs text-muted-foreground tabular-nums">
            {money(order.price)}
          </span>
        ) : null}
        <div className="ml-auto">
          <RefundStatusBadge status={order.refund_status} />
        </div>
      </div>
      {claims.length > 0 ? (
        <div className="space-y-1.5">
          {claims.map((claim) => (
            <ClaimRow key={claim.id} claim={claim} />
          ))}
        </div>
      ) : null}
      {chats.length > 0 ? (
        <div className="space-y-2">
          {chats.map((chat) => (
            <ChatCard key={chat.id} chat={chat} />
          ))}
        </div>
      ) : null}
    </div>
  )
}

export function RunDetailSheet({
  run,
  onClose,
}: {
  run: Run | null
  onClose: () => void
}) {
  // Keep the last selected run around so the close animation doesn't flash empty.
  // React's "adjust state during render" pattern: store the prop and, when it
  // changes to a non-null run, update synchronously in THIS render (no ref write,
  // no effect lag). `shown` is therefore in lockstep with `run` on open — so the
  // detailQuery below never sees `shown === null` while `open` is true.
  const [shown, setShown] = useState<Run | null>(run)
  const [prevRun, setPrevRun] = useState<Run | null>(run)
  if (run !== prevRun) {
    setPrevRun(run)
    if (run) setShown(run)
  }
  const open = run !== null

  const detailQuery = useQuery({
    queryKey: ["run-detail", shown?.id],
    queryFn: () => api.get<RunDetailResponse>(`/runs/${shown!.id}`),
    enabled: open,
    refetchInterval: run?.status === "running" ? 4000 : false,
  })

  if (!shown) return null

  const liveRun = detailQuery.data?.run ?? shown
  const orders = detailQuery.data?.orders
  const chats = detailQuery.data?.chats
  const claims = detailQuery.data?.claims
  // Per-order audit grouping (chats stacked by attempt + self-claim records).
  const grouped = detailQuery.data
    ? groupByOrder(detailQuery.data)
    : { perOrder: [], orphanChats: [] }
  const auditCount = grouped.perOrder.length + grouped.orphanChats.length

  // Prefer detail-derived counts (truthful mid-run); fall back to saved stats.
  const checked = orders ? orders.length : statNum(liveRun.stats, "checked")
  const missing = orders
    ? orders.filter((o) => o.refund_status === "not_refunded").length
    : statNum(liveRun.stats, "not_refunded")
  const chatCount = chats ? chats.length : statNum(liveRun.stats, "chats_started")
  const claimCount = claims ? claims.length : statNum(liveRun.stats, "claims_started")
  const chatsWon = chats
    ? chats.filter((c) => c.outcome === "success").length
    : statNum(liveRun.stats, "chats_won")
  const claimsWon = claims
    ? claims.filter((c) => c.outcome === "success").length
    : statNum(liveRun.stats, "claims_won")
  const resolved = chatsWon + claimsWon
  const duration = runDuration(liveRun)
  const hasErrors = (orders ?? []).some((o) => o.error)

  return (
    <Sheet
      open={open}
      onOpenChange={(nextOpen) => {
        if (!nextOpen) onClose()
      }}
    >
      <SheetContent side="right" className="w-full gap-0 data-[side=right]:sm:max-w-2xl">
        <SheetHeader className="border-b pr-12">
          <div className="flex flex-wrap items-center gap-2.5">
            <SheetTitle>Run #{shown.id}</SheetTitle>
            <RunStatusBadge status={liveRun.status} />
          </div>
          <SheetDescription className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
            <span>{format(parseDbDate(shown.started_at), "EEE, MMM d, yyyy · h:mm a")}</span>
            <span aria-hidden>·</span>
            <span>{scopeSummary(shown.scope)}</span>
            <span aria-hidden>·</span>
            <StrategyLabel strategy={shown.chat_strategy} />
          </SheetDescription>
        </SheetHeader>

        {/* The three answers, instantly: missing refunds / chats / ending. */}
        <div className="grid grid-cols-3 divide-x divide-border border-b bg-muted/20">
          <HeadlineStat
            label="Missing refunds"
            value={
              <span
                className={
                  missing > 0
                    ? "text-status-critical-fg"
                    : "text-status-success-fg"
                }
              >
                {missing}
              </span>
            }
            sub={checked > 0 ? `of ${checked} checked` : "nothing checked"}
          />
          <HeadlineStat
            label="Refunds pursued"
            value={chatCount + claimCount}
            sub={
              chatCount + claimCount > 0
                ? `${resolved} resolved${claimCount > 0 ? ` · ${claimCount} self-claim` : ""}`
                : liveRun.status === "running"
                  ? "so far"
                  : "none needed"
            }
          />
          <HeadlineStat
            label="Ended"
            value={<RunStatusBadge status={liveRun.status} />}
            sub={
              liveRun.status === "running"
                ? "in progress"
                : duration
                  ? `after ${duration}`
                  : undefined
            }
          />
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto">
          {detailQuery.isPending ? (
            <div className="space-y-4 p-4">
              <Skeleton className="h-4 w-36" />
              <Skeleton className="h-44 w-full rounded-xl" />
              <Skeleton className="h-4 w-32" />
              <Skeleton className="h-60 w-full rounded-xl" />
            </div>
          ) : detailQuery.isError ? (
            <div className="flex flex-col items-center gap-3 px-6 py-16 text-center">
              <AlertCircle className="size-6 text-status-critical-fg" />
              <p className="text-sm text-muted-foreground">
                Couldn't load the details for this run.
              </p>
              <Button
                variant="outline"
                size="sm"
                onClick={() => void detailQuery.refetch()}
              >
                Try again
              </Button>
            </div>
          ) : (
            <div className="space-y-6 p-4 pb-8">
              {/* Orders */}
              <section>
                <SectionHeading
                  icon={ShoppingBag}
                  title="Orders checked"
                  count={orders?.length ?? 0}
                />
                {!orders || orders.length === 0 ? (
                  <p className="rounded-lg border border-dashed px-4 py-6 text-center text-xs text-muted-foreground">
                    No orders were checked in this run.
                  </p>
                ) : (
                  <Card className="gap-0 overflow-hidden py-0">
                    <Table>
                      <TableHeader>
                        <TableRow className="bg-muted/40 hover:bg-muted/40">
                          <TableHead className="pl-3 text-xs">Store</TableHead>
                          <TableHead className="text-xs">Price</TableHead>
                          <TableHead className="text-xs">Refund</TableHead>
                          {hasErrors ? (
                            <TableHead className="text-xs">Error</TableHead>
                          ) : null}
                          <TableHead className="pr-3 text-xs">Screenshot</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {orders.map((order) => {
                          const refund = money(order.refund_amount ?? null)
                          const total = money(order.total_amount ?? null)
                          return (
                            <TableRow key={order.id} className="hover:bg-muted/30">
                              <TableCell className="py-2.5 pl-3">
                                <div className="flex items-center gap-1.5">
                                  <span className="max-w-44 truncate font-medium">
                                    {order.store_name || "Unknown store"}
                                  </span>
                                  {order.order_status === "cancelled" ? (
                                    <Badge
                                      variant="outline"
                                      className="h-4 border-status-critical/40 bg-status-critical/5 px-1.5 text-[10px] text-status-critical-fg"
                                    >
                                      Cancelled
                                    </Badge>
                                  ) : null}
                                </div>
                                {order.description ? (
                                  <div className="max-w-52 truncate text-xs text-muted-foreground">
                                    {order.description}
                                  </div>
                                ) : null}
                              </TableCell>
                              <TableCell
                                className={cn(
                                  "py-2.5 tabular-nums",
                                  order.price === null && "text-muted-foreground/50",
                                )}
                              >
                                {money(order.price) ?? "—"}
                              </TableCell>
                              <TableCell className="py-2.5">
                                <div className="flex items-center gap-2">
                                  <RefundStatusBadge status={order.refund_status} />
                                  {refund !== null || total !== null ? (
                                    <span className="text-xs text-muted-foreground tabular-nums">
                                      {refund ?? "—"} / {total ?? "—"}
                                    </span>
                                  ) : null}
                                </div>
                              </TableCell>
                              {hasErrors ? (
                                <TableCell className="py-2.5">
                                  {order.error ? (
                                    <span
                                      title={order.error}
                                      className="block max-w-36 truncate text-xs text-status-critical-fg"
                                    >
                                      {order.error}
                                    </span>
                                  ) : (
                                    <span className="text-xs text-muted-foreground/50">—</span>
                                  )}
                                </TableCell>
                              ) : null}
                              <TableCell className="py-2.5 pr-3">
                                {order.screenshot_path ? (
                                  <span
                                    title={order.screenshot_path}
                                    className="inline-flex max-w-40 items-center gap-1 text-muted-foreground/80"
                                  >
                                    <Camera className="size-3 shrink-0" />
                                    <span className="truncate font-mono text-[11px]">
                                      {basename(order.screenshot_path)}
                                    </span>
                                  </span>
                                ) : (
                                  <span className="text-xs text-muted-foreground/50">—</span>
                                )}
                              </TableCell>
                            </TableRow>
                          )
                        })}
                      </TableBody>
                    </Table>
                  </Card>
                )}
              </section>

              {/* Refund pursuits, grouped per order (chats stacked by attempt
                  + self-claim records). */}
              <section>
                <SectionHeading
                  icon={MessagesSquare}
                  title="Refund pursuits"
                  count={auditCount}
                />
                {auditCount === 0 ? (
                  <p className="rounded-lg border border-dashed px-4 py-6 text-center text-xs text-muted-foreground">
                    Nothing needed chasing — no self-claims or support chats.
                  </p>
                ) : (
                  <div className="space-y-3">
                    {grouped.perOrder.map((audit) => (
                      <OrderAuditCard key={audit.order.order_id} audit={audit} />
                    ))}
                    {/* Legacy customer-keyed chats with no resolvable order. */}
                    {grouped.orphanChats.map((chat) => (
                      <ChatCard key={chat.id} chat={chat} />
                    ))}
                  </div>
                )}
              </section>
            </div>
          )}
        </div>
      </SheetContent>
    </Sheet>
  )
}
