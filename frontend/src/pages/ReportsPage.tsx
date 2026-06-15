/**
 * Reports — the daily refund worklog, rendered NATIVELY at full width in the
 * app's brutalist style (no cramped iframe). A horizontal date strip selects
 * the day; the report itself fills the page: summary band, per-customer order
 * tables with How + confidence, hover-zoom proof screenshots, and chat
 * transcripts. "Open HTML" still links the standalone file for printing/sharing.
 */

import { useEffect, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { format, parseISO } from "date-fns"
import { AlertCircle, ExternalLink, FileText, RefreshCw } from "lucide-react"
import { EmptyState } from "@/components/empty-state"
import { PageHeader } from "@/components/page-header"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { ReportView } from "@/components/reports/report-view"
import { api } from "@/lib/api"
import { cn } from "@/lib/utils"
import type { ReportData } from "@/lib/types"

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
    return format(parseISO(date), "EEE, MMM d, yyyy")
  } catch {
    return date
  }
}

export default function ReportsPage() {
  const qc = useQueryClient()
  const [selected, setSelected] = useState<string | null>(null)

  const list = useQuery({
    queryKey: ["reports"],
    queryFn: () => api.get<ReportsResponse>("/reports"),
  })
  const reports = list.data?.reports ?? []

  useEffect(() => {
    if (selected === null && reports.length > 0) setSelected(reports[0].date)
  }, [reports, selected])

  const detail = useQuery({
    queryKey: ["report-data", selected],
    queryFn: () => api.get<ReportData>(`/reports/${selected}/data`),
    enabled: !!selected,
  })

  const rebuild = useMutation({
    mutationFn: (date: string) => api.post<{ url: string }>(`/reports/${date}/rebuild`),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["reports"] })
      void qc.invalidateQueries({ queryKey: ["report-data", selected] })
    },
  })

  return (
    <>
      <PageHeader
        title="Reports"
        description="Daily refund worklog — per-customer breakdown, refund method, chat transcripts, and proof screenshots."
        actions={
          <Button variant="outline" size="sm" onClick={() => void list.refetch()} disabled={list.isFetching}>
            <RefreshCw data-icon="inline-start" className={cn(list.isFetching && "animate-spin")} />
            Refresh
          </Button>
        }
      />

      {list.isPending ? (
        <div className="space-y-4">
          <Skeleton className="h-12 w-full" />
          <Skeleton className="h-[60vh] w-full" />
        </div>
      ) : list.isError ? (
        <div className="flex flex-col items-center gap-3 border border-border bg-card px-8 py-16 text-center">
          <AlertCircle className="size-6 text-primary" />
          <p className="text-sm text-muted-foreground">Couldn't load reports. Is the backend running?</p>
          <Button variant="outline" size="sm" onClick={() => void list.refetch()}>Try again</Button>
        </div>
      ) : reports.length === 0 ? (
        <EmptyState
          icon={FileText}
          title="No reports yet"
          description="A report is generated for each day you process refunds."
        />
      ) : (
        <div className="space-y-5">
          {/* Horizontal date strip — pick a day */}
          <div className="flex flex-wrap gap-2">
            {reports.map((r) => {
              const active = r.date === selected
              return (
                <button
                  key={r.date}
                  type="button"
                  onClick={() => setSelected(r.date)}
                  className={cn(
                    "border px-3.5 py-2 text-left transition-colors",
                    active
                      ? "border-primary bg-primary/10"
                      : "border-border bg-card hover:border-muted-foreground/50",
                  )}
                >
                  <div className="text-sm font-bold tracking-tight">{prettyDate(r.date)}</div>
                  <div className="mt-0.5 flex items-center gap-2 text-xs text-muted-foreground">
                    <span className="num">{r.customers}c · {r.orders}o</span>
                    <span className="text-emerald-500">{r.refunded} refunded</span>
                    {r.unconfirmed > 0 ? (
                      <span className="font-semibold text-amber-500">⚠ {r.unconfirmed}</span>
                    ) : null}
                  </div>
                </button>
              )
            })}
          </div>

          {/* Toolbar for the selected day */}
          {selected ? (
            <div className="flex items-center justify-between border-b border-border pb-2">
              <h2 className="text-xl font-bold tracking-tight">{prettyDate(selected)}</h2>
              <div className="flex items-center gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => rebuild.mutate(selected)}
                  disabled={rebuild.isPending}
                >
                  <RefreshCw data-icon="inline-start" className={cn(rebuild.isPending && "animate-spin")} />
                  Rebuild
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  render={<a href={`/report-files/${selected}.html`} target="_blank" rel="noreferrer" />}
                >
                  <ExternalLink data-icon="inline-start" />
                  Open HTML
                </Button>
              </div>
            </div>
          ) : null}

          {/* The native report, full width. Guard on `selected` so a disabled
              detail query (isPending=true before any date is chosen) doesn't
              flash a ghost skeleton + layout jump on first load. */}
          {!selected ? null : detail.isPending ? (
            <Skeleton className="h-[60vh] w-full" />
          ) : detail.isError ? (
            <div className="border border-border bg-card px-6 py-10 text-center text-sm text-muted-foreground">
              Couldn't load this report's data.
            </div>
          ) : detail.data ? (
            <ReportView data={detail.data} />
          ) : null}
        </div>
      )}
    </>
  )
}
