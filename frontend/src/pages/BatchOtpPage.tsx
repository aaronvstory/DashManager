/**
 * Batch OTP — live SMS codes for a batch of accounts CLAUDE created (named
 * "<label> - claude" in CustomerDaisy). Pick a batch, then this polls
 * /customers/daisy-batch-otps for the latest code per account so you can log
 * each one into a phone in one sitting. Mirrors LiveOtpPage (bucket version);
 * this one reads CustomerDaisy batches instead of DashManager buckets, because
 * batch-created accounts live in CustomerDaisy.
 */

import { useEffect, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Copy, Layers, RadioTower, RefreshCw } from "lucide-react"
import { toast } from "sonner"
import { EmptyState } from "@/components/empty-state"
import { PageHeader } from "@/components/page-header"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { api } from "@/lib/api"
import { cn } from "@/lib/utils"

const POLL_MS = 5000
const CODE_TTL_MS = 30000

interface BatchAccount {
  name: string
  email: string
  phone: string
  customer_id: string
}
interface Batch {
  batch_id: string
  batch_label: string
  count: number
  accounts: BatchAccount[]
}
interface BatchOtpRow {
  name: string
  email: string
  phone: string
  code: string
  error: string
}
interface BatchOtpResponse {
  rows: BatchOtpRow[]
  fetched_at: string
}

export default function BatchOtpPage() {
  const [batchId, setBatchId] = useState<string | null>(null)
  const [paused, setPaused] = useState(false)

  const batchesQ = useQuery({
    queryKey: ["daisy-batches"],
    queryFn: () => api.get<{ batches: Batch[] }>("/customers/daisy-batches"),
  })

  const batches = batchesQ.data?.batches ?? []

  useEffect(() => {
    if (batchId === null && batches.length > 0)
      setBatchId(batches[0].batch_id || batches[0].batch_label)
  }, [batches, batchId])

  const selected = batches.find(
    (b) => (b.batch_id || b.batch_label) === batchId,
  )

  const otpQ = useQuery({
    queryKey: ["daisy-batch-otps", batchId],
    queryFn: () => {
      const b = selected
      const q = b?.batch_id
        ? `batch_id=${encodeURIComponent(b.batch_id)}`
        : `batch_label=${encodeURIComponent(b?.batch_label ?? "")}`
      return api.get<BatchOtpResponse>(`/customers/daisy-batch-otps?${q}`)
    },
    enabled: !!selected,
    refetchInterval: paused ? false : POLL_MS,
  })

  const rows = otpQ.data?.rows ?? []
  const fetchedAt = otpQ.data?.fetched_at ?? null

  return (
    <>
      <PageHeader
        title="Batch OTP"
        description="Live SMS codes for a Claude-created batch — pick a batch, then grab each account's code to log into a phone. Codes expire ~30s."
        actions={
          <div className="flex items-center gap-2">
            <Button
              variant={paused ? "default" : "outline"}
              size="sm"
              onClick={() => setPaused((p) => !p)}
            >
              <RadioTower data-icon="inline-start" className={cn(!paused && "animate-pulse")} />
              {paused ? "Resume" : "Live"}
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => void otpQ.refetch()}
              disabled={!selected || otpQ.isFetching}
            >
              <RefreshCw data-icon="inline-start" className={cn(otpQ.isFetching && "animate-spin")} />
              Refresh
            </Button>
          </div>
        }
      />

      {batchesQ.isPending ? (
        <Skeleton className="h-64 w-full" />
      ) : batchesQ.isError ? (
        <div className="flex flex-col items-center gap-3 border border-border bg-card px-8 py-16 text-center">
          <p className="text-sm text-muted-foreground">
            Couldn't load batches. Is the backend running?
          </p>
          <Button variant="outline" size="sm" onClick={() => void batchesQ.refetch()}>
            Try again
          </Button>
        </div>
      ) : batches.length === 0 ? (
        <EmptyState
          icon={Layers}
          title="No batches yet"
          description="Create a batch of accounts (named '<label> - claude'); their live OTP codes show up here."
        />
      ) : (
        <div className="space-y-5">
          <div className="flex flex-wrap gap-2">
            {batches.map((b) => {
              const key = b.batch_id || b.batch_label
              const active = key === batchId
              return (
                <button
                  key={key}
                  type="button"
                  onClick={() => setBatchId(key)}
                  className={cn(
                    "border px-3.5 py-2 text-sm font-bold tracking-tight transition-colors",
                    active
                      ? "border-primary bg-primary/10"
                      : "border-border bg-card hover:border-muted-foreground/50",
                  )}
                >
                  {b.batch_label}
                  <span className="num ml-2 text-xs text-muted-foreground">
                    {b.count}
                  </span>
                </button>
              )
            })}
          </div>

          <BatchOtpTable
            rows={rows}
            fetchedAt={fetchedAt}
            loading={otpQ.isPending}
            errored={otpQ.isError}
            paused={paused}
          />
        </div>
      )}
    </>
  )
}

function BatchOtpTable({
  rows,
  fetchedAt,
  loading,
  errored,
  paused,
}: {
  rows: BatchOtpRow[]
  fetchedAt: string | null
  loading: boolean
  errored: boolean
  paused: boolean
}) {
  if (loading) return <Skeleton className="h-64 w-full" />
  if (errored) {
    return (
      <div className="border border-primary/40 bg-card px-6 py-12 text-center text-sm text-muted-foreground">
        Couldn't fetch OTP codes for this batch — the backend or api.cc bridge
        may be unavailable. Retrying on the next poll.
      </div>
    )
  }
  if (rows.length === 0) {
    return (
      <div className="border border-border bg-card px-6 py-12 text-center text-sm text-muted-foreground">
        No accounts in this batch.
      </div>
    )
  }

  return (
    <div className="border border-border bg-card">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border">
            <th className="eyebrow px-4 py-2 text-left">Name</th>
            <th className="eyebrow px-4 py-2 text-left">Email</th>
            <th className="eyebrow px-4 py-2 text-left">Phone</th>
            <th className="eyebrow px-4 py-2 text-left">Code</th>
            <th className="eyebrow px-4 py-2 text-left">Freshness</th>
            <th className="px-4 py-2" />
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <BatchOtpRowView
              key={`${r.email}-${i}`}
              row={r}
              fetchedAt={fetchedAt}
              paused={paused}
            />
          ))}
        </tbody>
      </table>
    </div>
  )
}

function BatchOtpRowView({
  row,
  fetchedAt,
  paused,
}: {
  row: BatchOtpRow
  fetchedAt: string | null
  paused: boolean
}) {
  const hasCode = !!row.code

  async function copy() {
    if (!row.code) return
    try {
      await navigator.clipboard.writeText(row.code)
      toast.success(`Copied ${row.name}'s code`)
    } catch {
      toast.error("Could not copy")
    }
  }

  return (
    <tr className="border-b border-border last:border-0">
      <td className="px-4 py-2.5 font-medium">{row.name}</td>
      <td className="px-4 py-2.5 text-muted-foreground">{row.email}</td>
      <td className="num px-4 py-2.5 text-muted-foreground">{row.phone}</td>
      <td className="px-4 py-2.5">
        {hasCode ? (
          <span className="num text-lg font-bold tracking-wide text-primary">
            {row.code}
          </span>
        ) : (
          <span className="text-xs text-muted-foreground">
            {row.error || "no code yet"}
          </span>
        )}
      </td>
      <td className="px-4 py-2.5">
        {hasCode ? (
          <FreshnessBar fetchedAt={fetchedAt} paused={paused} />
        ) : (
          <span className="text-xs text-muted-foreground">—</span>
        )}
      </td>
      <td className="px-4 py-2.5 text-right">
        {hasCode ? (
          <Button variant="outline" size="sm" onClick={() => void copy()}>
            <Copy data-icon="inline-start" />
            Copy
          </Button>
        ) : null}
      </td>
    </tr>
  )
}

function FreshnessBar({
  fetchedAt,
  paused,
}: {
  fetchedAt: string | null
  paused: boolean
}) {
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    if (paused) return
    const id = setInterval(() => setNow(Date.now()), 500)
    return () => clearInterval(id)
  }, [paused])

  // While paused the ticking interval is off, so a manual refresh (new
  // fetchedAt) would otherwise leave `now` stale and the bar frozen. Re-sync
  // `now` whenever fetchedAt changes so the freshness reflects the new code.
  useEffect(() => {
    setNow(Date.now())
  }, [fetchedAt])

  const base = fetchedAt ? new Date(fetchedAt).getTime() : now
  const elapsed = Math.max(0, now - base)
  const pct = Math.max(0, Math.min(1, 1 - elapsed / CODE_TTL_MS))
  const stale = pct <= 0

  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-20 overflow-hidden bg-muted">
        <div
          className={cn(
            "h-full transition-[width] duration-500 ease-linear",
            stale ? "bg-zinc-500" : pct < 0.34 ? "bg-amber-500" : "bg-emerald-500",
          )}
          style={{ width: `${pct * 100}%` }}
        />
      </div>
      <span className="num text-[0.7rem] text-muted-foreground">
        {stale ? "expiring" : `${Math.ceil((pct * CODE_TTL_MS) / 1000)}s`}
      </span>
    </div>
  )
}
