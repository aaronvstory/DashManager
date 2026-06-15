/**
 * Live OTP — an auto-refreshing grid of the latest SMS codes for a bucket, like
 * CustomerDaisy's "Live SMS Codes". api.cc codes expire in ~30s, so this polls
 * /customers/otp-live every few seconds and shows a freshness bar per code.
 *
 * Two ways to grab a code (the handoff asked for BOTH): this batch live-table
 * for a whole bucket, AND the existing per-customer Fetch-OTP button on the
 * Customers page (which blocks up to ~2 min for a fresh SMS). This page is the
 * "X at a time" view for logging several accounts into a phone in one sitting.
 */

import { useEffect, useMemo, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { format, parseISO } from "date-fns"
import { Copy, RadioTower, RefreshCw, Smartphone } from "lucide-react"
import { toast } from "sonner"
import { EmptyState } from "@/components/empty-state"
import { PageHeader } from "@/components/page-header"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { api } from "@/lib/api"
import { cn } from "@/lib/utils"
import type { Customer } from "@/lib/types"

const POLL_MS = 5000
const CODE_TTL_MS = 30000 // api.cc codes expire ~30s

interface OtpRow {
  id: number
  name: string
  phone: string
  code: string
  error: string
}

interface OtpLiveResponse {
  rows: OtpRow[]
  fetched_at: string
}

function prettyDate(date: string): string {
  try {
    return format(parseISO(date), "EEE, MMM d")
  } catch {
    return date
  }
}

export default function LiveOtpPage() {
  const [bucket, setBucket] = useState<string | null>(null)
  const [paused, setPaused] = useState(false)

  // Customers drive the bucket picker + the "is this bucket empty" check.
  const customersQ = useQuery({
    queryKey: ["customers"],
    queryFn: () => api.get<{ customers: Customer[] }>("/customers"),
  })

  const buckets = useMemo(() => {
    const set = new Set<string>()
    for (const c of customersQ.data?.customers ?? []) set.add(c.bucket_date)
    return [...set].sort((a, b) => (a < b ? 1 : -1))
  }, [customersQ.data])

  useEffect(() => {
    if (bucket === null && buckets.length > 0) setBucket(buckets[0])
  }, [buckets, bucket])

  const otpQ = useQuery({
    queryKey: ["otp-live", bucket],
    queryFn: () =>
      api.get<OtpLiveResponse>(`/customers/otp-live?bucket_date=${bucket}`),
    // enabled gates on the bucket only (NOT pause) — disabling a query also
    // suppresses manual refetch(), which would make the Refresh button a no-op
    // while paused. Pause just stops the auto-poll interval.
    enabled: !!bucket,
    refetchInterval: paused ? false : POLL_MS,
  })

  const rows = otpQ.data?.rows ?? []
  const fetchedAt = otpQ.data?.fetched_at ?? null

  return (
    <>
      <PageHeader
        title="Live OTP"
        description="Latest SMS codes for a bucket, auto-refreshing — for logging several accounts into a phone at once. Codes expire ~30s; the bar shows freshness."
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
              disabled={!bucket || otpQ.isFetching}
            >
              <RefreshCw data-icon="inline-start" className={cn(otpQ.isFetching && "animate-spin")} />
              Refresh
            </Button>
          </div>
        }
      />

      {customersQ.isPending ? (
        <Skeleton className="h-64 w-full" />
      ) : buckets.length === 0 ? (
        <EmptyState
          icon={Smartphone}
          title="No customers yet"
          description="Create or import customers with a rented number, then their live OTP codes show up here."
        />
      ) : (
        <div className="space-y-5">
          {/* Bucket picker */}
          <div className="flex flex-wrap gap-2">
            {buckets.map((d) => {
              const active = d === bucket
              return (
                <button
                  key={d}
                  type="button"
                  onClick={() => setBucket(d)}
                  className={cn(
                    "border px-3.5 py-2 text-sm font-bold tracking-tight transition-colors",
                    active
                      ? "border-primary bg-primary/10"
                      : "border-border bg-card hover:border-muted-foreground/50",
                  )}
                >
                  {prettyDate(d)}
                </button>
              )
            })}
          </div>

          {/* Live table */}
          <OtpTable
            rows={rows}
            fetchedAt={fetchedAt}
            loading={otpQ.isPending}
            paused={paused}
          />
        </div>
      )}
    </>
  )
}

function OtpTable({
  rows,
  fetchedAt,
  loading,
  paused,
}: {
  rows: OtpRow[]
  fetchedAt: string | null
  loading: boolean
  paused: boolean
}) {
  if (loading) return <Skeleton className="h-64 w-full" />
  if (rows.length === 0) {
    return (
      <div className="border border-border bg-card px-6 py-12 text-center text-sm text-muted-foreground">
        No customers with a rented number in this bucket.
      </div>
    )
  }

  return (
    <div className="border border-border bg-card">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border">
            <th className="eyebrow px-4 py-2 text-left">Name</th>
            <th className="eyebrow px-4 py-2 text-left">Phone</th>
            <th className="eyebrow px-4 py-2 text-left">Code</th>
            <th className="eyebrow px-4 py-2 text-left">Freshness</th>
            <th className="px-4 py-2" />
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <OtpTableRow
              key={r.id}
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

function OtpTableRow({
  row,
  fetchedAt,
  paused,
}: {
  row: OtpRow
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

/**
 * A shrinking bar that counts the ~30s api.cc code lifetime down from the last
 * fetch. Purely visual freshness cue — the next poll replaces the code anyway.
 */
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
