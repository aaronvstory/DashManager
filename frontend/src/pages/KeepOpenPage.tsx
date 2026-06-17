/**
 * Keep Open — hold customers' Chromium windows open (already logged in) between
 * refund runs, so you can eyeball accounts or log them in without re-launching.
 *
 * Windows are app-owned (backend keep_open_manager): they live until you close
 * them or the server restarts; the on-disk login persists either way. Starting
 * a refund run on a customer AUTO-CLOSES its kept-open window first (the run and
 * keep-open share one Chromium profile lock — only one can hold it).
 *
 * Authoritative open-state comes from GET /api/keep-open; we refetch whenever a
 * keep_open_* SSE event lands so several tabs / the run loop stay in sync.
 */

import { useEffect, useMemo } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { format } from "date-fns"
import { Info, MonitorPlay, MonitorX, PanelsTopLeft } from "lucide-react"
import { toast } from "sonner"
import { EmptyState } from "@/components/empty-state"
import { PageHeader } from "@/components/page-header"
import { SessionStatusBadge } from "@/components/customers/session-status-badge"
import { parseBucketDate } from "@/components/customers/helpers"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { api } from "@/lib/api"
import { useRunStore } from "@/store/runStore"
import { TONE } from "@/lib/status-tone"
import { cn } from "@/lib/utils"
import type { Customer } from "@/lib/types"

interface KeepOpenStatus {
  /** Windows this server process is holding open right now. */
  open_ids: number[]
}

// parseBucketDate tolerates non-date suffixes like "2026-06-17-failed" (it
// reads only the leading y-m-d), so failed-signup buckets still get a real
// label instead of the raw string.
function prettyDate(bucket: string): string {
  return format(parseBucketDate(bucket), "EEE, MMM d")
}

export default function KeepOpenPage() {
  const queryClient = useQueryClient()
  const lastEvent = useRunStore((s) => s.lastEvent)

  const customersQ = useQuery({
    queryKey: ["customers"],
    queryFn: () => api.get<{ customers: Customer[] }>("/customers"),
  })
  const statusQ = useQuery({
    queryKey: ["keep-open"],
    queryFn: () => api.get<KeepOpenStatus>("/keep-open"),
    refetchInterval: 15000, // safety net; events drive most refreshes
  })

  // Any keep_open_* event (from this tab, another tab, or a run auto-closing a
  // window) → re-pull the authoritative open set.
  useEffect(() => {
    if (lastEvent?.type?.startsWith("keep_open")) {
      void queryClient.invalidateQueries({ queryKey: ["keep-open"] })
    }
  }, [lastEvent, queryClient])

  const openIds = useMemo(
    () => new Set(statusQ.data?.open_ids ?? []),
    [statusQ.data],
  )

  const buckets = useMemo(() => {
    const list = customersQ.data?.customers ?? []
    const byDate = new Map<string, Customer[]>()
    for (const c of list) {
      const arr = byDate.get(c.bucket_date) ?? []
      arr.push(c)
      byDate.set(c.bucket_date, arr)
    }
    return [...byDate.entries()].sort((a, b) => b[0].localeCompare(a[0]))
  }, [customersQ.data])

  async function openIdsReq(ids: number[]) {
    if (ids.length === 0) return
    try {
      await api.post("/keep-open", { ids })
      void queryClient.invalidateQueries({ queryKey: ["keep-open"] })
    } catch {
      toast.error("Couldn't open the browser window(s) — is the backend up?")
    }
  }
  async function closeIdsReq(ids: number[]) {
    if (ids.length === 0) return
    try {
      await api.post("/keep-open/close", { ids })
      void queryClient.invalidateQueries({ queryKey: ["keep-open"] })
    } catch {
      toast.error("Couldn't close the browser window(s) — is the backend up?")
    }
  }

  return (
    <>
      <PageHeader
        title="Keep Open"
        description="Hold customer browser windows open (already logged in) between runs — to spot-check accounts or log them in. Starting a refund run on a customer closes its window first; the on-disk login always persists."
        actions={
          openIds.size > 0 ? (
            <Button
              variant="outline"
              onClick={() => void closeIdsReq([...openIds])}
            >
              <MonitorX data-icon="inline-start" />
              Close all ({openIds.size})
            </Button>
          ) : null
        }
      />

      <div className="mb-6 flex items-start gap-2 border border-border bg-muted/30 px-3 py-2 text-sm text-muted-foreground">
        <Info className="mt-0.5 size-4 shrink-0" />
        <span>
          Windows are released automatically when a refund run starts on that
          customer, then re-opened by the run — so you never double-open one
          profile. They also close on server restart.
        </span>
      </div>

      {/* Wait for BOTH the customer list and the authoritative open-state:
          rendering rows before statusQ resolves would briefly show every
          customer as closed and invite redundant Open clicks. */}
      {customersQ.isLoading || statusQ.isLoading ? (
        <div className="space-y-3">
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-24 w-full" />
        </div>
      ) : customersQ.isError || statusQ.isError ? (
        <div className="flex flex-col items-center gap-3 border border-border bg-card px-8 py-16 text-center">
          <p className="text-sm text-muted-foreground">
            Couldn't load keep-open state. Is the backend running?
          </p>
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              void customersQ.refetch()
              void statusQ.refetch()
            }}
          >
            Try again
          </Button>
        </div>
      ) : buckets.length === 0 ? (
        <EmptyState
          icon={PanelsTopLeft}
          title="No customers yet"
          description="Create or adopt accounts first — then keep their browsers open here."
        />
      ) : (
        <div className="space-y-6">
          {buckets.map(([date, custs]) => {
            const ids = custs.map((c) => c.id)
            const allOpen = ids.every((id) => openIds.has(id))
            return (
              <section key={date} className="border border-border bg-card">
                <header className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-4 py-2.5">
                  <div className="flex items-baseline gap-2">
                    <h2 className="text-sm font-semibold">{prettyDate(date)}</h2>
                    <span className="num text-xs text-muted-foreground">
                      {custs.length} customer{custs.length === 1 ? "" : "s"}
                    </span>
                  </div>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={allOpen}
                    onClick={() =>
                      void openIdsReq(ids.filter((id) => !openIds.has(id)))
                    }
                  >
                    <MonitorPlay data-icon="inline-start" />
                    {allOpen ? "All open" : "Open all"}
                  </Button>
                </header>
                <ul className="divide-y divide-border">
                  {custs.map((c) => {
                    const isOpen = openIds.has(c.id)
                    const name =
                      `${c.first_name} ${c.last_name}`.trim() ||
                      `Customer ${c.id}`
                    return (
                      <li
                        key={c.id}
                        className="flex flex-wrap items-center gap-3 px-4 py-2.5"
                      >
                        <span
                          aria-hidden
                          className={cn(
                            "size-2 shrink-0 rounded-full",
                            isOpen ? "bg-status-success" : "bg-muted-foreground/40",
                          )}
                        />
                        <span className="min-w-0 flex-1 truncate font-medium">
                          {name}
                        </span>
                        <SessionStatusBadge status={c.session_status} />
                        {isOpen ? (
                          <span
                            className={cn(
                              "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium",
                              TONE.success,
                            )}
                          >
                            Open
                          </span>
                        ) : null}
                        {isOpen ? (
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => void closeIdsReq([c.id])}
                          >
                            <MonitorX data-icon="inline-start" />
                            Close
                          </Button>
                        ) : (
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => void openIdsReq([c.id])}
                          >
                            <MonitorPlay data-icon="inline-start" />
                            Open
                          </Button>
                        )}
                      </li>
                    )
                  })}
                </ul>
              </section>
            )
          })}
        </div>
      )}
    </>
  )
}
