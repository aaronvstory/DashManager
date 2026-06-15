/**
 * Proxies — the residential-proxy manager. DoorDash *signup* needs a US
 * residential egress IP (the bot gate flags datacenter ranges); the user's
 * LightningProxies gateway lines live in `working-proxies.txt` (gitignored).
 *
 * This page lists each configured proxy (host:port + the geo/rotation label —
 * NEVER the password, which never leaves the backend) and runs a liveness test
 * THROUGH each one: alive?, exit IP, country/city, latency, and whether the
 * exit IP differs from the PC's own IP (the "only the browser, not the whole
 * PC" proof). Brutalist: hard borders, mono data, uppercase micro-labels.
 */

import { useMutation, useQuery } from "@tanstack/react-query"
import { Activity, Globe, ShieldCheck, Zap } from "lucide-react"
import { EmptyState } from "@/components/empty-state"
import { PageHeader } from "@/components/page-header"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { api } from "@/lib/api"
import { cn } from "@/lib/utils"

interface ProxyRow {
  id: string
  scheme: string
  host: string
  port: string
  label: string
}

interface ProxyListResponse {
  configured: boolean
  count: number
  proxies: ProxyRow[]
}

interface ProxyResult {
  id: string
  alive: boolean
  exit_ip: string
  country: string
  city: string
  region: string
  latency_ms: number | null
  error: string
  differs_from_local: boolean | null
}

interface TestAllResponse {
  local_ip: string
  count: number
  alive_count: number
  proxies: ProxyResult[]
}

function latencyTone(ms: number | null): string {
  if (ms === null) return "text-muted-foreground"
  if (ms < 800) return "text-emerald-500"
  if (ms < 2000) return "text-amber-500"
  return "text-primary"
}

export default function ProxiesPage() {
  const list = useQuery({
    queryKey: ["proxies"],
    queryFn: () => api.get<ProxyListResponse>("/proxies"),
  })

  const testAll = useMutation({
    mutationFn: () => api.post<TestAllResponse>("/proxies/test"),
  })

  const proxies = list.data?.proxies ?? []
  const results = testAll.data?.proxies ?? []
  const byId = new Map(results.map((r) => [r.id, r]))
  const localIp = testAll.data?.local_ip ?? ""

  return (
    <>
      <PageHeader
        title="Proxies"
        description="Residential proxies for DoorDash signup. Test each line to confirm it's alive and routes through a US residential exit IP ≠ this PC's IP. Credentials never leave the backend."
        actions={
          <Button
            size="sm"
            onClick={() => testAll.mutate()}
            disabled={testAll.isPending || proxies.length === 0}
          >
            <Activity data-icon="inline-start" className={cn(testAll.isPending && "animate-pulse")} />
            {testAll.isPending ? "Testing…" : "Test all"}
          </Button>
        }
      />

      {list.isPending ? (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-40 w-full" />
          ))}
        </div>
      ) : list.isError ? (
        <div className="flex flex-col items-center gap-3 border border-border bg-card px-8 py-16 text-center">
          <p className="text-sm text-muted-foreground">Couldn't load proxies. Is the backend running?</p>
          <Button variant="outline" size="sm" onClick={() => void list.refetch()}>
            Try again
          </Button>
        </div>
      ) : !list.data?.configured || proxies.length === 0 ? (
        <EmptyState
          icon={Globe}
          title="No proxies configured"
          description="Add residential proxy lines to working-proxies.txt in the repo root (gitignored). Format: http://host:port:user:pass — one per line."
        />
      ) : (
        <div className="space-y-5">
          {/* Summary band — shows the PC's own IP + alive count after a test */}
          <div className="flex flex-wrap items-center gap-x-8 gap-y-2 border border-border bg-card px-5 py-3">
            <Stat label="Proxies" value={String(list.data.count)} />
            <Stat
              label="Alive"
              value={
                testAll.data
                  ? `${testAll.data.alive_count} / ${testAll.data.count}`
                  : "—"
              }
              tone={testAll.data && testAll.data.alive_count > 0 ? "text-emerald-500" : undefined}
            />
            <Stat label="This PC's IP" value={localIp || "—"} mono />
            {testAll.isError ? (
              <span className="text-xs font-semibold uppercase tracking-wide text-primary">
                Test failed — see backend logs
              </span>
            ) : null}
          </div>

          {/* Proxy grid */}
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {proxies.map((p) => (
              <ProxyCard
                key={p.id}
                proxy={p}
                result={byId.get(p.id)}
                pending={testAll.isPending}
                localIp={localIp}
              />
            ))}
          </div>
        </div>
      )}
    </>
  )
}

function Stat({
  label,
  value,
  tone,
  mono,
}: {
  label: string
  value: string
  tone?: string
  mono?: boolean
}) {
  return (
    <div className="flex flex-col">
      <span className="eyebrow">{label}</span>
      <span className={cn("text-lg font-bold tracking-tight", mono && "num text-base", tone)}>
        {value}
      </span>
    </div>
  )
}

function ProxyCard({
  proxy,
  result,
  pending,
  localIp,
}: {
  proxy: ProxyRow
  result: ProxyResult | undefined
  pending: boolean
  localIp: string
}) {
  const tested = !!result
  const alive = result?.alive ?? false
  // Tri-state routing proof (the backend only sets differs_from_local when it
  // could fetch the PC's own IP). true → routes elsewhere ✓; false → exit IP
  // equals the PC's IP (proxy not routing) ⚠; null → comparison not done, so
  // we say nothing rather than falsely claiming success.
  const samePc = result?.differs_from_local === false
  const routesElsewhere = result?.differs_from_local === true

  return (
    <div
      className={cn(
        "flex flex-col gap-3 border bg-card p-4",
        tested
          ? alive
            ? samePc
              ? "border-amber-500/60"
              : "border-emerald-500/50"
            : "border-primary/50"
          : "border-border",
      )}
    >
      {/* Header: status dot + host:port */}
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="num truncate text-sm font-semibold">
            {proxy.host}:{proxy.port}
          </div>
          <div className="eyebrow mt-0.5 truncate" title={proxy.label}>
            {proxy.label || proxy.scheme}
          </div>
        </div>
        <StatusDot tested={tested} alive={alive} pending={pending} />
      </div>

      {/* Body: results or prompt */}
      {pending && !tested ? (
        <div className="text-xs text-muted-foreground">Testing…</div>
      ) : !tested ? (
        <div className="text-xs text-muted-foreground">Not tested yet.</div>
      ) : alive ? (
        <div className="space-y-1.5">
          <Field icon={Globe} label="Exit IP">
            <span className="num">{result?.exit_ip || "—"}</span>
          </Field>
          <Field icon={ShieldCheck} label="Location">
            {[result?.city, result?.region, result?.country]
              .filter(Boolean)
              .join(", ") || "—"}
          </Field>
          <Field icon={Zap} label="Latency">
            <span className={cn("num", latencyTone(result?.latency_ms ?? null))}>
              {result?.latency_ms !== null ? `${result?.latency_ms} ms` : "—"}
            </span>
          </Field>
          {samePc ? (
            <div className="mt-1 border border-amber-500/40 bg-amber-500/10 px-2 py-1 text-[0.7rem] font-medium text-amber-600 dark:text-amber-400">
              ⚠ Exit IP equals this PC's IP ({localIp}) — proxy may not be routing.
            </div>
          ) : routesElsewhere ? (
            <div className="mt-1 text-[0.7rem] text-emerald-600 dark:text-emerald-400">
              ✓ Routes through a different IP than this PC.
            </div>
          ) : (
            <div className="mt-1 text-[0.7rem] text-muted-foreground">
              Couldn't compare against this PC's IP.
            </div>
          )}
        </div>
      ) : (
        <div className="space-y-1.5">
          <div className="text-xs font-semibold uppercase tracking-wide text-primary">Dead</div>
          {result?.error ? (
            <div className="break-words text-[0.7rem] text-muted-foreground">{result.error}</div>
          ) : null}
        </div>
      )}
    </div>
  )
}

function StatusDot({
  tested,
  alive,
  pending,
}: {
  tested: boolean
  alive: boolean
  pending: boolean
}) {
  const tone = !tested
    ? pending
      ? "bg-amber-500 animate-pulse"
      : "bg-zinc-500"
    : alive
      ? "bg-emerald-500"
      : "bg-primary"
  return (
    <span className="mt-1 flex size-2.5 shrink-0">
      <span className={cn("inline-flex size-2.5 rounded-full", tone)} />
    </span>
  )
}

function Field({
  icon: Icon,
  label,
  children,
}: {
  // Generic so any Lucide icon (Globe, ShieldCheck, Zap, …) is accepted.
  icon: React.ComponentType<{ className?: string }>
  label: string
  children: React.ReactNode
}) {
  return (
    <div className="flex items-center gap-2 text-xs">
      <Icon className="size-3.5 shrink-0 text-muted-foreground" />
      <span className="eyebrow w-16 shrink-0">{label}</span>
      <span className="min-w-0 truncate text-foreground">{children}</span>
    </div>
  )
}
