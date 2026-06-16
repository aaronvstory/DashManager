/**
 * CustomerDaisy coverage analytics — totals + verified split + top states/cities.
 * Read-only summary over CustomerDaisy's pool, backed by GET /api/daisy/analytics
 * (computed in the worker from the customers DB). Zeros when the DB is absent.
 */
import { useQuery } from "@tanstack/react-query"
import { BarChart3 } from "lucide-react"
import { Skeleton } from "@/components/ui/skeleton"
import { api } from "@/lib/api"

interface Bucket {
  key: string
  count: number
}

interface Analytics {
  total: number
  verified: number
  unverified: number
  by_state: Bucket[]
  by_city: Bucket[]
}

const TOP_N = 6

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="border border-border bg-background px-4 py-3">
      <div className="font-mono text-2xl font-semibold tabular-nums">{value}</div>
      <div className="eyebrow mt-1">{label}</div>
    </div>
  )
}

function BucketList({ title, buckets, total }: {
  title: string
  buckets: Bucket[]
  total: number
}) {
  const top = buckets.slice(0, TOP_N)
  const rest = buckets.length - top.length
  const max = top.reduce((m, b) => Math.max(m, b.count), 0) || 1
  return (
    <div className="border border-border bg-background">
      <div className="eyebrow border-b border-border px-4 py-2">{title}</div>
      {top.length === 0 ? (
        <p className="px-4 py-3 text-sm text-muted-foreground">No data.</p>
      ) : (
        <ul className="divide-y divide-border">
          {top.map((b) => (
            <li key={b.key} className="px-4 py-2">
              <div className="flex items-baseline justify-between gap-3">
                <span className="truncate text-sm">{b.key}</span>
                <span className="num shrink-0 text-sm text-muted-foreground">
                  {b.count}
                  {total > 0 && (
                    <span className="ml-1 text-xs">
                      ({Math.round((b.count / total) * 100)}%)
                    </span>
                  )}
                </span>
              </div>
              {/* a tiny bar, relative to the top bucket — purely visual */}
              <div className="mt-1 h-1 w-full bg-border">
                <div
                  className="h-full bg-primary"
                  style={{ width: `${(b.count / max) * 100}%` }}
                />
              </div>
            </li>
          ))}
        </ul>
      )}
      {rest > 0 && (
        <div className="border-t border-border px-4 py-2 text-xs text-muted-foreground">
          +{rest} more
        </div>
      )}
    </div>
  )
}

export function DaisyAnalytics() {
  const q = useQuery({
    queryKey: ["daisy-analytics"],
    queryFn: () => api.get<Analytics>("/daisy/analytics"),
  })

  return (
    <section className="mt-10 border border-border bg-card">
      <header className="flex items-center gap-2 border-b border-border px-4 py-3">
        <BarChart3 className="size-4 text-muted-foreground" />
        <h2 className="font-mono text-sm font-semibold uppercase tracking-wide">
          Coverage Analytics
        </h2>
      </header>

      {q.isPending ? (
        <Skeleton className="m-4 h-48" />
      ) : q.isError ? (
        <p className="px-4 py-6 text-sm text-muted-foreground">
          Couldn't load analytics — is CustomerDaisy's path set in Settings?
        </p>
      ) : (
        <div className="space-y-4 p-4">
          <div className="grid grid-cols-3 gap-3">
            <Stat label="Total accounts" value={q.data.total} />
            <Stat label="Verified" value={q.data.verified} />
            <Stat label="Unverified" value={q.data.unverified} />
          </div>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <BucketList title="By state" buckets={q.data.by_state}
                        total={q.data.total} />
            <BucketList title="By city" buckets={q.data.by_city}
                        total={q.data.total} />
          </div>
        </div>
      )}
    </section>
  )
}
