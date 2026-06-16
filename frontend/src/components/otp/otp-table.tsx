import { Copy } from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { FreshnessBar } from "./freshness-bar"

/** A live SMS-code row. `id` is present in bucket mode (the customer's DB id,
    stable across polls); `email` is shown only in batch mode. */
export interface OtpRow {
  id?: number
  name: string
  phone: string
  code: string
  error: string
  email?: string
}

/**
 * The live OTP grid shared by the Bucket and Batch views: name · [email] ·
 * phone · code · freshness · copy. Codes auto-refresh; the bar shows the ~30s
 * api.cc lifetime. `showEmail` adds the email column (batch mode).
 */
export function OtpTable({
  rows,
  fetchedAt,
  loading,
  errored,
  paused,
  showEmail,
  emptyText,
}: {
  rows: OtpRow[]
  fetchedAt: string | null
  loading: boolean
  errored?: boolean
  paused: boolean
  showEmail?: boolean
  emptyText: string
}) {
  if (loading) return <Skeleton className="h-64 w-full" />
  if (errored) {
    return (
      <div className="border border-primary/40 bg-card px-6 py-12 text-center text-sm text-muted-foreground">
        Couldn't fetch OTP codes — the backend or api.cc bridge may be
        unavailable. Retrying on the next poll.
      </div>
    )
  }
  if (rows.length === 0) {
    return (
      <div className="border border-border bg-card px-6 py-12 text-center text-sm text-muted-foreground">
        {emptyText}
      </div>
    )
  }

  return (
    <div className="border border-border bg-card">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border">
            <th className="eyebrow px-4 py-2 text-left">Name</th>
            {showEmail ? (
              <th className="eyebrow px-4 py-2 text-left">Email</th>
            ) : null}
            <th className="eyebrow px-4 py-2 text-left">Phone</th>
            <th className="eyebrow px-4 py-2 text-left">Code</th>
            <th className="eyebrow px-4 py-2 text-left">Freshness</th>
            <th className="px-4 py-2" />
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <OtpTableRow
              // Prefer the customer DB id (stable across polls, bucket mode);
              // fall back to phone+index for batch rows that carry no id.
              key={r.id != null ? String(r.id) : `${r.phone}-${i}`}
              row={r}
              fetchedAt={fetchedAt}
              paused={paused}
              showEmail={showEmail}
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
  showEmail,
}: {
  row: OtpRow
  fetchedAt: string | null
  paused: boolean
  showEmail?: boolean
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
      {showEmail ? (
        <td className="px-4 py-2.5 text-muted-foreground">{row.email}</td>
      ) : null}
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
