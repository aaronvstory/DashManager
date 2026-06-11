/**
 * Slide-over audit view for a single run: headline numbers (missing refunds /
 * chats / how it ended), every checked order, and full chat transcripts.
 *
 * Detail is fetched lazily when the sheet opens; while the run is still
 * running we poll so the audit trail fills in live.
 */

import { useRef } from "react"
import type { ReactNode } from "react"
import { useQuery } from "@tanstack/react-query"
import { format } from "date-fns"
import { AlertCircle, Camera, MessagesSquare, ShoppingBag } from "lucide-react"
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
import type { Run } from "@/lib/types"
import { cn } from "@/lib/utils"
import {
  ChatOutcomeBadge,
  RefundStatusBadge,
  RunStatusBadge,
  StrategyLabel,
} from "./badges"
import { ChatTranscript } from "./chat-transcript"
import {
  basename,
  money,
  parseDbDate,
  runDuration,
  scopeSummary,
  statNum,
} from "./run-data"
import type { RunDetailResponse } from "./run-data"

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

export function RunDetailSheet({
  run,
  onClose,
}: {
  run: Run | null
  onClose: () => void
}) {
  // Keep the last selected run around so the close animation doesn't flash empty.
  const lastRef = useRef<Run | null>(null)
  if (run) lastRef.current = run
  const shown = run ?? lastRef.current
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

  // Prefer detail-derived counts (truthful mid-run); fall back to saved stats.
  const checked = orders ? orders.length : statNum(liveRun.stats, "checked")
  const missing = orders
    ? orders.filter((o) => o.refund_status === "not_refunded").length
    : statNum(liveRun.stats, "not_refunded")
  const chatCount = chats ? chats.length : statNum(liveRun.stats, "chats_started")
  const chatsWon = chats
    ? chats.filter((c) => c.outcome === "success").length
    : statNum(liveRun.stats, "chats_won")
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
                    ? "text-red-600 dark:text-red-400"
                    : "text-emerald-600 dark:text-emerald-400"
                }
              >
                {missing}
              </span>
            }
            sub={checked > 0 ? `of ${checked} checked` : "nothing checked"}
          />
          <HeadlineStat
            label="Chats opened"
            value={chatCount}
            sub={
              chatCount > 0
                ? `${chatsWon} won`
                : liveRun.status === "running"
                  ? "so far"
                  : "none opened"
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
              <AlertCircle className="size-6 text-red-500" />
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
                                      className="h-4 border-red-500/40 bg-red-500/5 px-1.5 text-[10px] text-red-600 dark:text-red-400"
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
                                      className="block max-w-36 truncate text-xs text-red-600 dark:text-red-400"
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

              {/* Chats */}
              <section>
                <SectionHeading
                  icon={MessagesSquare}
                  title="Support chats"
                  count={chats?.length ?? 0}
                />
                {!chats || chats.length === 0 ? (
                  <p className="rounded-lg border border-dashed px-4 py-6 text-center text-xs text-muted-foreground">
                    No support chats were opened — nothing needed chasing.
                  </p>
                ) : (
                  <div className="space-y-3">
                    {chats.map((chat) => (
                      <Card key={chat.id} size="sm" className="gap-0 overflow-hidden py-0">
                        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b bg-muted/30 px-3 py-2">
                          <span className="text-xs font-medium">Chat #{chat.id}</span>
                          <span className="text-xs text-muted-foreground">
                            Customer {chat.customer_id} · {chat.order_ids.length}{" "}
                            order{chat.order_ids.length === 1 ? "" : "s"}
                          </span>
                          <div className="ml-auto flex items-center gap-2.5">
                            {chat.agent_reached ? (
                              <span className="inline-flex items-center gap-1 text-[11px] font-medium text-emerald-600 dark:text-emerald-400">
                                <span className="size-1.5 rounded-full bg-emerald-500" />
                                Agent reached
                              </span>
                            ) : (
                              <span className="inline-flex items-center gap-1 text-[11px] text-muted-foreground">
                                <span className="size-1.5 rounded-full bg-zinc-500" />
                                No agent
                              </span>
                            )}
                            <ChatOutcomeBadge outcome={chat.outcome} />
                          </div>
                        </div>
                        <ChatTranscript messages={chat.messages} />
                      </Card>
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
