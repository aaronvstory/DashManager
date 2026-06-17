import { useEffect, useState } from "react"
import { useMutation, useQuery } from "@tanstack/react-query"
import { Link } from "react-router-dom"
import { format, parseISO } from "date-fns"
import { History, Loader2, OctagonX, Radar } from "lucide-react"
import { toast } from "sonner"
import { Badge } from "@/components/ui/badge"
import { Button, buttonVariants } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { EmptyState } from "@/components/empty-state"
import { PageHeader } from "@/components/page-header"
import { StrategyBadge } from "@/components/run/badges"
import { ChatsPanel } from "@/components/run/chats-panel"
import { CustomerProgressStrip } from "@/components/run/customer-progress-strip"
import { LiveLog } from "@/components/run/live-log"
import { LiveOrdersTable } from "@/components/run/live-orders-table"
import { StartRunCard } from "@/components/run/start-run-card"
import { StatCards } from "@/components/run/stat-cards"
import { api } from "@/lib/api"
import type { Run, StrategyName } from "@/lib/types"
import { TONE } from "@/lib/status-tone"
import { useRunStore } from "@/store/runStore"
import { cn } from "@/lib/utils"

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function scopeSummary(scope: Record<string, unknown> | null): string {
  if (!scope) return "Active run"
  const bucket = scope.bucket_date
  if (typeof bucket === "string" && bucket) {
    try {
      return `Bucket ${format(parseISO(bucket), "MMM d, yyyy")}`
    } catch {
      return `Bucket ${bucket}`
    }
  }
  const ids = scope.customer_ids
  if (Array.isArray(ids)) {
    return `${ids.length} selected customer${ids.length === 1 ? "" : "s"}`
  }
  return "Active run"
}

// ---------------------------------------------------------------------------
// Active-run view
// ---------------------------------------------------------------------------

function ActiveRunView() {
  const runId = useRunStore((s) => s.runId)
  const startedScope = useRunStore((s) => s.startedScope)
  const chatStrategy = useRunStore((s) => s.chatStrategy)
  const [stopOpen, setStopOpen] = useState(false)

  // Page opened mid-run (adopted via /runs/active): no run_started event was
  // seen, so backfill scope/strategy from the run record.
  const detailQuery = useQuery({
    queryKey: ["runs", runId, "header"],
    queryFn: () => api.get<{ run: Run }>(`/runs/${runId}`),
    enabled: runId !== null && startedScope === null,
    staleTime: Infinity,
  })
  const scope = startedScope ?? detailQuery.data?.run.scope ?? null
  const strategy: StrategyName | null =
    chatStrategy ?? detailQuery.data?.run.chat_strategy ?? null

  const stopRun = useMutation({
    mutationFn: () => api.post<{ stopping: boolean }>("/runs/stop"),
    onSuccess: () => {
      setStopOpen(false)
      toast.info("Stopping run", {
        description: "The run will halt after the current customer.",
      })
    },
    onError: () => toast.error("Failed to stop the run."),
  })

  return (
    <>
      <PageHeader
        title="Run in progress"
        description={runId !== null ? `Run #${runId} — events streaming live.` : undefined}
        actions={
          <>
            <Badge variant="outline" className={cn("gap-1.5", TONE.success)}>
              <span className="relative flex size-1.5">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-status-success/70" />
                <span className="relative inline-flex size-1.5 rounded-full bg-status-success" />
              </span>
              {scopeSummary(scope)}
            </Badge>
            {strategy ? <StrategyBadge strategy={strategy} /> : null}
            <Button
              variant="destructive"
              onClick={() => setStopOpen(true)}
              disabled={stopRun.isPending}
            >
              {stopRun.isPending ? (
                <Loader2 data-icon="inline-start" className="animate-spin" />
              ) : (
                <OctagonX data-icon="inline-start" />
              )}
              Stop run
            </Button>
          </>
        }
      />

      <div className="space-y-6">
        <StatCards />
        <CustomerProgressStrip />
        <div className="grid gap-6 xl:grid-cols-2">
          <LiveOrdersTable />
          <ChatsPanel />
        </div>
        <LiveLog />
      </div>

      <Dialog open={stopOpen} onOpenChange={setStopOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Stop this run?</DialogTitle>
            <DialogDescription>
              The run finishes the customer currently being processed, then
              halts. Checked orders and chat transcripts are kept.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <DialogClose render={<Button variant="outline" />}>
              Keep running
            </DialogClose>
            <Button
              variant="destructive"
              onClick={() => stopRun.mutate()}
              disabled={stopRun.isPending}
            >
              {stopRun.isPending ? (
                <Loader2 data-icon="inline-start" className="animate-spin" />
              ) : (
                <OctagonX data-icon="inline-start" />
              )}
              Stop run
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}

// ---------------------------------------------------------------------------
// Idle view
// ---------------------------------------------------------------------------

function FinishedRunSummary() {
  const runId = useRunStore((s) => s.runId)
  const stats = useRunStore((s) => s.stats)
  if (runId === null || Object.keys(stats).length === 0) return null

  const items: Array<{ label: string; value: number; className?: string }> = [
    { label: "Customers", value: stats.customers ?? 0 },
    { label: "Checked", value: stats.checked ?? 0 },
    {
      label: "Missing refund",
      value: stats.not_refunded ?? 0,
      className: "text-status-critical-fg",
    },
    {
      label: "Chats won",
      value: stats.chats_won ?? 0,
      className: "text-status-success-fg",
    },
    {
      label: "Blocked",
      value: stats.blocked ?? 0,
      className: "text-status-warning-fg",
    },
    { label: "Errors", value: stats.errors ?? 0 },
  ]

  return (
    <Card size="sm" className="mb-6">
      <CardContent className="flex flex-wrap items-center gap-x-8 gap-y-3">
        <div className="text-sm font-medium">Last run #{runId}</div>
        {items.map((it) => (
          <div key={it.label} className="flex items-baseline gap-1.5">
            <span className={cn("text-lg font-semibold tabular-nums", it.className)}>
              {it.value}
            </span>
            <span className="text-xs text-muted-foreground">{it.label}</span>
          </div>
        ))}
        <Link
          to="/history"
          className={cn(buttonVariants({ variant: "ghost", size: "sm" }), "ml-auto")}
        >
          <History data-icon="inline-start" />
          View in history
        </Link>
      </CardContent>
    </Card>
  )
}

function IdleView() {
  return (
    <>
      <PageHeader
        title="Refund Run"
        description="Launch a refund-check run and watch orders, chats, and outcomes stream in live."
      />
      <FinishedRunSummary />
      <div className="grid items-start gap-6 lg:grid-cols-[1fr_24rem]">
        <EmptyState
          icon={Radar}
          title="No active run"
          description="Start a run to scrape orders, detect missing refunds, and open support chats automatically."
        />
        <StartRunCard />
      </div>
    </>
  )
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function RunPage() {
  const runActive = useRunStore((s) => s.runActive)
  const setActiveRun = useRunStore((s) => s.setActiveRun)

  // If the page was opened mid-run, adopt the in-flight run id.
  const activeQuery = useQuery({
    queryKey: ["runs", "active"],
    queryFn: () => api.get<{ run_id: number | null }>("/runs/active"),
  })
  useEffect(() => {
    const runId = activeQuery.data?.run_id
    if (typeof runId === "number" && !useRunStore.getState().runActive) {
      setActiveRun(runId)
    }
  }, [activeQuery.data, setActiveRun])

  return runActive ? <ActiveRunView /> : <IdleView />
}
