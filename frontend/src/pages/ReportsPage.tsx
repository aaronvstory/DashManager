/**
 * Reports — the daily refund worklog. Lists every generated report by date
 * (with a quick refunded/pursuing summary) and previews the full HTML report
 * inline. Each report already contains the per-customer breakdown, the chat
 * transcripts, the refund method (self-claim / chat / already refunded), and
 * the proof screenshots — this page just makes them findable from the app.
 */

import { useEffect, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { format, parseISO } from "date-fns"
import {
  AlertCircle,
  ExternalLink,
  FileText,
  RefreshCw,
} from "lucide-react"
import { EmptyState } from "@/components/empty-state"
import { PageHeader } from "@/components/page-header"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { api } from "@/lib/api"
import { cn } from "@/lib/utils"

interface ReportSummary {
  date: string
  url: string
  customers: number
  orders: number
  refunded: number
  pursuing: number
  unconfirmed: number
  needs_you: number
}

interface ReportsResponse {
  reports: ReportSummary[]
}

function prettyDate(date: string): string {
  try {
    return format(parseISO(date), "EEEE, MMMM d, yyyy")
  } catch {
    return date
  }
}

export default function ReportsPage() {
  const qc = useQueryClient()
  const [selected, setSelected] = useState<string | null>(null)

  const { data, isPending, isError, isFetching, refetch } = useQuery({
    queryKey: ["reports"],
    queryFn: () => api.get<ReportsResponse>("/reports"),
  })

  const reports = data?.reports ?? []

  // Default the preview to the newest report once the list loads.
  useEffect(() => {
    if (selected === null && reports.length > 0) setSelected(reports[0].date)
  }, [reports, selected])

  const rebuild = useMutation({
    mutationFn: (date: string) =>
      api.post<{ url: string }>(`/reports/${date}/rebuild`),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["reports"] })
      // Bust the iframe cache so the rebuilt HTML actually reloads.
      setReloadKey((k) => k + 1)
    },
  })

  const [reloadKey, setReloadKey] = useState(0)
  const current = reports.find((r) => r.date === selected) ?? null

  return (
    <>
      <PageHeader
        title="Reports"
        description="The daily refund worklog — per-customer breakdown, chat transcripts, refund method, and proof screenshots, one report per day."
        actions={
          <>
            {reports.length > 0 ? (
              <span className="text-sm text-muted-foreground tabular-nums">
                {reports.length} {reports.length === 1 ? "report" : "reports"}
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
        <div className="flex gap-6">
          <div className="w-72 shrink-0 space-y-3">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-20 w-full rounded-xl" />
            ))}
          </div>
          <Skeleton className="h-[70vh] flex-1 rounded-xl" />
        </div>
      ) : isError ? (
        <Card className="flex flex-col items-center gap-3 border-dashed px-8 py-16 text-center shadow-none">
          <AlertCircle className="size-6 text-red-500" />
          <p className="text-sm text-muted-foreground">
            Couldn't load reports. Is the backend running?
          </p>
          <Button variant="outline" size="sm" onClick={() => void refetch()}>
            Try again
          </Button>
        </Card>
      ) : reports.length === 0 ? (
        <EmptyState
          icon={FileText}
          title="No reports yet"
          description="A report is generated for each day you process refunds. Run a batch and the daily report shows up here."
        />
      ) : (
        <div className="flex flex-col gap-6 lg:flex-row">
          {/* Date list */}
          <div className="w-full shrink-0 space-y-2.5 lg:w-72">
            {reports.map((r) => {
              const active = r.date === selected
              return (
                <button
                  key={r.date}
                  type="button"
                  onClick={() => setSelected(r.date)}
                  className={cn(
                    "w-full rounded-xl border p-4 text-left transition-colors",
                    active
                      ? "border-primary/40 bg-primary/5"
                      : "border-border hover:border-muted-foreground/30 hover:bg-muted/40",
                  )}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-medium tracking-tight">
                      {prettyDate(r.date)}
                    </span>
                  </div>
                  <div className="mt-1 font-mono text-xs text-muted-foreground">
                    {r.date}
                  </div>
                  <div className="mt-3 flex flex-wrap items-center gap-1.5">
                    <Badge
                      variant="outline"
                      className="border-emerald-500/25 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
                    >
                      {r.refunded} refunded
                    </Badge>
                    {r.unconfirmed > 0 ? (
                      <Badge
                        variant="outline"
                        className="border-amber-500/40 bg-amber-500/15 font-semibold text-amber-600 dark:text-amber-400"
                      >
                        ⚠ {r.unconfirmed} unconfirmed
                      </Badge>
                    ) : null}
                    {r.pursuing - r.unconfirmed > 0 ? (
                      <Badge
                        variant="outline"
                        className="border-amber-500/25 bg-amber-500/10 text-amber-600 dark:text-amber-400"
                      >
                        {r.pursuing - r.unconfirmed} pursuing
                      </Badge>
                    ) : null}
                    <span className="text-xs text-muted-foreground tabular-nums">
                      {r.customers} cust · {r.orders} ord
                    </span>
                  </div>
                </button>
              )
            })}
          </div>

          {/* Preview */}
          <Card className="flex min-h-[70vh] flex-1 flex-col gap-0 overflow-hidden p-0">
            {current ? (
              <>
                <div className="flex items-center justify-between gap-3 border-b border-border px-4 py-2.5">
                  <span className="text-sm font-medium">
                    {prettyDate(current.date)}
                  </span>
                  <div className="flex items-center gap-2">
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => rebuild.mutate(current.date)}
                      disabled={rebuild.isPending}
                    >
                      <RefreshCw
                        data-icon="inline-start"
                        className={cn(rebuild.isPending && "animate-spin")}
                      />
                      Rebuild
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      render={
                        <a href={current.url} target="_blank" rel="noreferrer" />
                      }
                    >
                      <ExternalLink data-icon="inline-start" />
                      Open
                    </Button>
                  </div>
                </div>
                <iframe
                  key={`${current.date}-${reloadKey}`}
                  title={`Report ${current.date}`}
                  src={current.url}
                  className="min-h-[68vh] w-full flex-1 bg-white"
                />
              </>
            ) : null}
          </Card>
        </div>
      )}
    </>
  )
}
