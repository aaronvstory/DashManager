/**
 * History — the audit trail. Lists every run with its scope, strategy,
 * status, and result chips; clicking a row opens the detail sheet with
 * checked orders and full chat transcripts.
 */

import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { useNavigate } from "react-router-dom"
import { AlertCircle, ChevronRight, History, Play, RefreshCw } from "lucide-react"
import { EmptyState } from "@/components/empty-state"
import { PageHeader } from "@/components/page-header"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { RunStatusBadge, StatChips, StrategyLabel } from "@/components/history/badges"
import { RunDetailSheet } from "@/components/history/run-detail-sheet"
import { formatDbDate, scopeSummary } from "@/components/history/run-data"
import type { RunsResponse } from "@/components/history/run-data"
import { api } from "@/lib/api"
import { cn } from "@/lib/utils"

function LoadingRows() {
  return (
    <Card className="gap-0 py-0">
      <div className="divide-y divide-border">
        {Array.from({ length: 5 }).map((_, i) => (
          <div key={i} className="flex items-center gap-4 px-4 py-3.5">
            <Skeleton className="h-4 w-10" />
            <Skeleton className="h-4 w-44" />
            <Skeleton className="h-4 w-28" />
            <Skeleton className="h-5 w-24 rounded-full" />
            <Skeleton className="ml-auto h-5 w-40 rounded-full" />
          </div>
        ))}
      </div>
    </Card>
  )
}

export default function HistoryPage() {
  const navigate = useNavigate()
  const [selectedId, setSelectedId] = useState<number | null>(null)

  const { data, isPending, isError, isFetching, refetch } = useQuery({
    queryKey: ["runs"],
    queryFn: () => api.get<RunsResponse>("/runs"),
    // Keep the list fresh while a run is in flight.
    refetchInterval: (query) =>
      query.state.data?.runs.some((r) => r.status === "running") ? 4000 : false,
  })

  const runs = data?.runs ?? []
  // Resolve from the latest fetch so the sheet header tracks status changes.
  const selected =
    selectedId === null ? null : (runs.find((r) => r.id === selectedId) ?? null)

  return (
    <>
      <PageHeader
        title="History"
        description="Past runs with their stats, chat transcripts, and refund outcomes."
        actions={
          <>
            {runs.length > 0 ? (
              <span className="text-sm text-muted-foreground tabular-nums">
                {runs.length} {runs.length === 1 ? "run" : "runs"}
              </span>
            ) : null}
            <Button
              variant="outline"
              size="sm"
              onClick={() => void refetch()}
              disabled={isFetching}
            >
              <RefreshCw
                data-icon="inline-start"
                className={cn(isFetching && "animate-spin")}
              />
              Refresh
            </Button>
          </>
        }
      />

      {isPending ? (
        <LoadingRows />
      ) : isError ? (
        <Card className="flex flex-col items-center gap-3 border-dashed px-8 py-16 text-center shadow-none">
          <AlertCircle className="size-6 text-status-critical-fg" />
          <p className="text-sm text-muted-foreground">
            Couldn't load run history. Is the backend running?
          </p>
          <Button variant="outline" size="sm" onClick={() => void refetch()}>
            Try again
          </Button>
        </Card>
      ) : runs.length === 0 ? (
        <EmptyState
          icon={History}
          title="No runs recorded"
          description="Once a run completes it shows up here with per-customer results and full chat transcripts."
          action={
            <Button onClick={() => navigate("/run")}>
              <Play data-icon="inline-start" />
              Start your first run
            </Button>
          }
        />
      ) : (
        <Card className="gap-0 overflow-hidden py-0">
          <Table>
            <TableHeader>
              <TableRow className="bg-muted/40 hover:bg-muted/40">
                <TableHead className="w-16 pl-4 text-xs">Run</TableHead>
                <TableHead className="text-xs">Started</TableHead>
                <TableHead className="text-xs">Scope</TableHead>
                <TableHead className="text-xs">Chat</TableHead>
                <TableHead className="text-xs">Status</TableHead>
                <TableHead className="text-xs">Results</TableHead>
                <TableHead className="w-8" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {runs.map((run) => (
                <TableRow
                  key={run.id}
                  tabIndex={0}
                  onClick={() => setSelectedId(run.id)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault()
                      setSelectedId(run.id)
                    }
                  }}
                  className="group cursor-pointer focus-visible:bg-muted/50 focus-visible:outline-none [&>td]:py-3"
                >
                  <TableCell className="pl-4 font-medium tabular-nums">
                    #{run.id}
                  </TableCell>
                  <TableCell className="text-muted-foreground tabular-nums">
                    {formatDbDate(run.started_at, "MMM d, yyyy · h:mm a")}
                  </TableCell>
                  <TableCell>{scopeSummary(run.scope)}</TableCell>
                  <TableCell>
                    <StrategyLabel strategy={run.chat_strategy} className="text-xs" />
                  </TableCell>
                  <TableCell>
                    <RunStatusBadge status={run.status} />
                  </TableCell>
                  <TableCell>
                    <StatChips stats={run.stats} />
                  </TableCell>
                  <TableCell className="pr-3 text-right">
                    <ChevronRight className="ml-auto size-4 text-muted-foreground/40 transition-colors group-hover:text-muted-foreground" />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Card>
      )}

      <RunDetailSheet run={selected} onClose={() => setSelectedId(null)} />
    </>
  )
}
