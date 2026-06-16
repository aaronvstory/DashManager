/**
 * OTP — live SMS codes, auto-refreshing. One page, two modes:
 *  - By bucket: every DashManager customer in a date bucket (/customers/otp-live)
 *  - By batch:  a Claude-created CustomerDaisy batch (/customers/daisy-batch-otps),
 *               with "Add to batch" to grow it mid-session.
 * api.cc codes expire ~30s, so this polls every few seconds and shows a
 * freshness bar per code. Replaces the former separate Live-OTP + Batch-OTP
 * pages (same UX, different source).
 */

import { useEffect, useMemo, useState } from "react"
import { useSearchParams } from "react-router-dom"
import { useQuery } from "@tanstack/react-query"
import { format, parseISO } from "date-fns"
import { Layers, RadioTower, RefreshCw, Smartphone, UserPlus } from "lucide-react"
import { CreateAccountDialog } from "@/components/customers/create-account-dialog"
import { EmptyState } from "@/components/empty-state"
import { OtpTable, type OtpRow } from "@/components/otp/otp-table"
import { OTP_POLL_MS } from "@/components/otp/freshness-bar"
import { PageHeader } from "@/components/page-header"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { api } from "@/lib/api"
import { cn } from "@/lib/utils"
import type { Customer } from "@/lib/types"

type Mode = "bucket" | "batch"

interface OtpResponse {
  rows: OtpRow[]
  fetched_at: string
}

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

function prettyDate(date: string): string {
  try {
    return format(parseISO(date), "EEE, MMM d")
  } catch {
    return date
  }
}

export default function OtpPage() {
  // ?mode=batch (e.g. the legacy /batch-otp redirect) opens the batch view.
  const [searchParams] = useSearchParams()
  const [mode, setMode] = useState<Mode>(
    searchParams.get("mode") === "batch" ? "batch" : "bucket",
  )
  const [paused, setPaused] = useState(false)

  // Switching mode mounts a fresh sub-view with new queries — resume live so it
  // doesn't silently start paused just because the other mode was paused.
  function selectMode(m: Mode) {
    setMode(m)
    setPaused(false)
  }

  return (
    <>
      <PageHeader
        title="OTP"
        description="Live SMS codes, auto-refreshing — for logging several accounts into a phone at once. Codes expire ~30s; the bar shows freshness."
        actions={
          <div className="flex items-center gap-2">
            {/* Mode toggle: by DashManager bucket, or by Claude-created batch. */}
            <div className="flex overflow-hidden border border-border">
              <button
                type="button"
                onClick={() => selectMode("bucket")}
                className={cn(
                  "px-3 py-1.5 text-sm font-medium transition-colors",
                  mode === "bucket"
                    ? "bg-primary/10 text-foreground"
                    : "bg-card text-muted-foreground hover:text-foreground",
                )}
              >
                By bucket
              </button>
              <button
                type="button"
                onClick={() => selectMode("batch")}
                className={cn(
                  "border-l border-border px-3 py-1.5 text-sm font-medium transition-colors",
                  mode === "batch"
                    ? "bg-primary/10 text-foreground"
                    : "bg-card text-muted-foreground hover:text-foreground",
                )}
              >
                By batch
              </button>
            </div>
            <Button
              variant={paused ? "default" : "outline"}
              size="sm"
              onClick={() => setPaused((p) => !p)}
            >
              <RadioTower
                data-icon="inline-start"
                className={cn(!paused && "animate-pulse")}
              />
              {paused ? "Resume" : "Live"}
            </Button>
          </div>
        }
      />

      {mode === "bucket" ? (
        <BucketOtp paused={paused} />
      ) : (
        <BatchOtp paused={paused} />
      )}
    </>
  )
}

function BucketOtp({ paused }: { paused: boolean }) {
  const [bucket, setBucket] = useState<string | null>(null)

  const customersQ = useQuery({
    queryKey: ["customers"],
    queryFn: () => api.get<{ customers: Customer[] }>("/customers"),
  })

  const buckets = useMemo(() => {
    const set = new Set<string>()
    for (const c of customersQ.data?.customers ?? []) set.add(c.bucket_date)
    return [...set].sort((a, b) => (a < b ? 1 : -1))
  }, [customersQ.data])

  // Pick the newest bucket, and recover if a background refetch drops the
  // currently-selected one (stale selection → empty table otherwise).
  useEffect(() => {
    if (buckets.length > 0 && (bucket === null || !buckets.includes(bucket)))
      setBucket(buckets[0])
  }, [buckets, bucket])

  const otpQ = useQuery({
    queryKey: ["otp-live", bucket],
    queryFn: () =>
      api.get<OtpResponse>(`/customers/otp-live?bucket_date=${bucket}`),
    // enabled gates on the bucket only (NOT pause) — disabling a query also
    // suppresses manual refetch(). Pause just stops the auto-poll interval.
    enabled: !!bucket,
    refetchInterval: paused ? false : OTP_POLL_MS,
  })

  if (customersQ.isPending) return <Skeleton className="h-64 w-full" />
  if (customersQ.isError) {
    return (
      <div className="flex flex-col items-center gap-3 border border-border bg-card px-8 py-16 text-center">
        <p className="text-sm text-muted-foreground">
          Couldn't load customers. Is the backend running?
        </p>
        <Button
          variant="outline"
          size="sm"
          onClick={() => void customersQ.refetch()}
        >
          Try again
        </Button>
      </div>
    )
  }
  if (buckets.length === 0) {
    return (
      <EmptyState
        icon={Smartphone}
        title="No customers yet"
        description="Create or import customers with a rented number, then their live OTP codes show up here."
      />
    )
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex flex-wrap gap-2">
          {buckets.map((d) => (
            <SelectorPill
              key={d}
              active={d === bucket}
              onClick={() => setBucket(d)}
              label={prettyDate(d)}
            />
          ))}
        </div>
        <RefreshButton
          onClick={() => void otpQ.refetch()}
          disabled={!bucket || otpQ.isFetching}
          spinning={otpQ.isFetching}
        />
      </div>

      <OtpTable
        rows={otpQ.data?.rows ?? []}
        fetchedAt={otpQ.data?.fetched_at ?? null}
        loading={otpQ.isPending}
        errored={otpQ.isError}
        paused={paused}
        emptyText="No customers with a rented number in this bucket."
      />
    </div>
  )
}

function BatchOtp({ paused }: { paused: boolean }) {
  const [batchId, setBatchId] = useState<string | null>(null)
  const [addOpen, setAddOpen] = useState(false)

  const batchesQ = useQuery({
    queryKey: ["daisy-batches"],
    queryFn: () => api.get<{ batches: Batch[] }>("/customers/daisy-batches"),
  })

  const batches = batchesQ.data?.batches ?? []

  // Pick the first batch, and recover if a refetch drops the selected one.
  useEffect(() => {
    if (batches.length === 0) return
    const exists = batches.some(
      (b) => (b.batch_id || b.batch_label) === batchId,
    )
    if (batchId === null || !exists)
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
      return api.get<OtpResponse>(`/customers/daisy-batch-otps?${q}`)
    },
    enabled: !!selected,
    refetchInterval: paused ? false : OTP_POLL_MS,
  })

  if (batchesQ.isPending) return <Skeleton className="h-64 w-full" />
  if (batchesQ.isError) {
    return (
      <div className="flex flex-col items-center gap-3 border border-border bg-card px-8 py-16 text-center">
        <p className="text-sm text-muted-foreground">
          Couldn't load batches. Is the backend running?
        </p>
        <Button
          variant="outline"
          size="sm"
          onClick={() => void batchesQ.refetch()}
        >
          Try again
        </Button>
      </div>
    )
  }
  if (batches.length === 0) {
    return (
      <EmptyState
        icon={Layers}
        title="No batches yet"
        description="Create a batch of accounts (named '<label> - claude'); their live OTP codes show up here."
      />
    )
  }

  return (
    <>
      <div className="space-y-5">
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex flex-wrap gap-2">
            {batches.map((b) => {
              const key = b.batch_id || b.batch_label
              return (
                <SelectorPill
                  key={key}
                  active={key === batchId}
                  onClick={() => setBatchId(key)}
                  label={b.batch_label}
                  count={b.count}
                />
              )
            })}
          </div>
          <Button
            size="sm"
            onClick={() => setAddOpen(true)}
            disabled={!selected}
            title={
              selected
                ? `Add an account to ${selected.batch_label}`
                : "Pick a batch first"
            }
          >
            <UserPlus data-icon="inline-start" />
            Add to batch
          </Button>
          <RefreshButton
            onClick={() => void otpQ.refetch()}
            disabled={!selected || otpQ.isFetching}
            spinning={otpQ.isFetching}
          />
        </div>

        <OtpTable
          rows={otpQ.data?.rows ?? []}
          fetchedAt={otpQ.data?.fetched_at ?? null}
          loading={otpQ.isPending}
          errored={otpQ.isError}
          paused={paused}
          showEmail
          emptyText="No accounts in this batch."
        />
      </div>

      {/* Add-one-to-batch: opens the create dialog seeded with this batch, so
          the new account joins it (same batch_id) instead of starting a new
          batch. */}
      {selected ? (
        <CreateAccountDialog
          open={addOpen}
          onOpenChange={setAddOpen}
          initialBatch={{
            batch_id: selected.batch_id,
            batch_label: selected.batch_label,
          }}
        />
      ) : null}
    </>
  )
}

function SelectorPill({
  active,
  onClick,
  label,
  count,
}: {
  active: boolean
  onClick: () => void
  label: string
  count?: number
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "border px-3.5 py-2 text-sm font-bold tracking-tight transition-colors",
        active
          ? "border-primary bg-primary/10"
          : "border-border bg-card hover:border-muted-foreground/50",
      )}
    >
      {label}
      {count != null ? (
        <span className="num ml-2 text-xs text-muted-foreground">{count}</span>
      ) : null}
    </button>
  )
}

function RefreshButton({
  onClick,
  disabled,
  spinning,
}: {
  onClick: () => void
  disabled: boolean
  spinning: boolean
}) {
  return (
    <Button
      variant="outline"
      size="sm"
      onClick={onClick}
      disabled={disabled}
      className="ml-auto"
    >
      <RefreshCw
        data-icon="inline-start"
        className={cn(spinning && "animate-spin")}
      />
      Refresh
    </Button>
  )
}
