/**
 * Proxies — the residential-proxy manager. DoorDash *signup* needs a US
 * residential egress IP (the bot gate flags datacenter ranges); the user's
 * proxy lines live in `working-proxies.txt` (gitignored).
 *
 * Manage them fully in-app: quick-add or bulk-paste (any format —
 * host:port:user:pass, user:pass@host:port, with/without http://|socks5://),
 * test each (alive? exit IP, geo, latency, routes-≠-this-PC proof), copy each
 * field, and delete dead/unwanted lines. Passwords stay on the backend except
 * an explicit per-proxy "copy line".
 */

import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  Activity,
  Globe,
  Loader2,
  Plus,
  ShieldCheck,
  Trash2,
  Zap,
} from "lucide-react"
import { toast } from "sonner"
import { CopyValue } from "@/components/customers/copy-cell"
import { EmptyState } from "@/components/empty-state"
import { PageHeader } from "@/components/page-header"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Textarea } from "@/components/ui/textarea"
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

interface AddResponse {
  added: number
  parsed: number
  errors: { line: string; error: string }[]
}

interface ProxyLine {
  scheme: string
  host: string
  port: string
  username: string
  password: string
  line: string
}

function latencyTone(ms: number | null): string {
  if (ms === null) return "text-muted-foreground"
  if (ms < 800) return "text-status-success-fg"
  if (ms < 2000) return "text-status-warning-fg"
  return "text-primary"
}

/** SOCKS vs HTTP at a glance — socks5 gets the accent, http stays muted. */
function SchemeBadge({ scheme }: { scheme: string }) {
  const isSocks = scheme.toLowerCase().startsWith("socks")
  return (
    <span
      className={cn(
        "shrink-0 border px-1.5 py-0.5 text-[0.6rem] font-bold uppercase tracking-wide",
        isSocks
          ? "border-status-info/40 bg-status-info/10 text-status-info-fg"
          : "border-border bg-muted/40 text-muted-foreground",
      )}
    >
      {scheme || "http"}
    </span>
  )
}

export default function ProxiesPage() {
  const qc = useQueryClient()
  const [addOpen, setAddOpen] = useState(false)

  const list = useQuery({
    queryKey: ["proxies"],
    queryFn: () => api.get<ProxyListResponse>("/proxies"),
  })

  const testAll = useMutation({
    mutationFn: () => api.post<TestAllResponse>("/proxies/test"),
  })

  // Per-proxy results accumulate here: from a "Test all" OR an individual test.
  const [results, setResults] = useState<Map<string, ProxyResult>>(new Map())
  const [localIp, setLocalIp] = useState("")
  const [testingId, setTestingId] = useState<string | null>(null)

  const proxies = list.data?.proxies ?? []

  async function runTestAll() {
    try {
      const res = await testAll.mutateAsync()
      setLocalIp(res.local_ip)
      setResults(new Map(res.proxies.map((r) => [r.id, r])))
    } catch {
      toast.error("Test failed — see backend logs")
    }
  }

  async function testOne(id: string) {
    setTestingId(id)
    try {
      const r = await api.post<ProxyResult & { local_ip: string }>(
        `/proxies/test/${encodeURIComponent(id)}`,
      )
      setLocalIp((cur) => r.local_ip || cur)
      setResults((m) => new Map(m).set(id, r))
    } catch {
      toast.error("Proxy test failed")
    } finally {
      setTestingId(null)
    }
  }

  async function deleteOne(id: string) {
    try {
      await api.del(`/proxies/${encodeURIComponent(id)}`)
      setResults((m) => {
        const n = new Map(m)
        n.delete(id)
        return n
      })
      toast.success("Proxy deleted")
      void qc.invalidateQueries({ queryKey: ["proxies"] })
    } catch {
      toast.error("Could not delete proxy")
    }
  }

  async function deleteDead() {
    const dead = proxies.filter((p) => {
      const r = results.get(p.id)
      return r && !r.alive
    })
    if (dead.length === 0) {
      toast.info("No tested-dead proxies to delete")
      return
    }
    let removed = 0
    for (const p of dead) {
      try {
        await api.del(`/proxies/${encodeURIComponent(p.id)}`)
        removed += 1
      } catch {
        // skip; report what we managed
      }
    }
    toast.success(`Deleted ${removed} dead prox${removed === 1 ? "y" : "ies"}`)
    void qc.invalidateQueries({ queryKey: ["proxies"] })
  }

  const deadCount = proxies.filter((p) => {
    const r = results.get(p.id)
    return r && !r.alive
  }).length

  return (
    <>
      <PageHeader
        title="Proxies"
        description="Residential proxies for DoorDash signup. Add/paste lines, test each (alive? US residential exit IP ≠ this PC?), copy fields, delete dead ones. Passwords stay on the backend."
        actions={
          <div className="flex items-center gap-2">
            {deadCount > 0 ? (
              <Button variant="outline" size="sm" onClick={() => void deleteDead()}>
                <Trash2 data-icon="inline-start" />
                Delete dead ({deadCount})
              </Button>
            ) : null}
            <Button
              variant="outline"
              size="sm"
              onClick={() => void runTestAll()}
              disabled={testAll.isPending || proxies.length === 0}
            >
              <Activity
                data-icon="inline-start"
                className={cn(testAll.isPending && "animate-pulse")}
              />
              {testAll.isPending ? "Testing…" : "Test all"}
            </Button>
            <Button size="sm" onClick={() => setAddOpen(true)}>
              <Plus data-icon="inline-start" />
              Add proxies
            </Button>
          </div>
        }
      />

      {list.isPending ? (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-44 w-full" />
          ))}
        </div>
      ) : list.isError ? (
        <div className="flex flex-col items-center gap-3 border border-border bg-card px-8 py-16 text-center">
          <p className="text-sm text-muted-foreground">
            Couldn't load proxies. Is the backend running?
          </p>
          <Button variant="outline" size="sm" onClick={() => void list.refetch()}>
            Try again
          </Button>
        </div>
      ) : !list.data?.configured || proxies.length === 0 ? (
        <EmptyState
          icon={Globe}
          title="No proxies yet"
          description="Add residential proxy lines — host:port:user:pass (with or without http://|socks5://). Click “Add proxies”."
          action={
            <Button onClick={() => setAddOpen(true)}>
              <Plus data-icon="inline-start" />
              Add proxies
            </Button>
          }
        />
      ) : (
        <div className="space-y-5">
          <div className="flex flex-wrap items-center gap-x-8 gap-y-2 border border-border bg-card px-5 py-3">
            <Stat label="Proxies" value={String(list.data.count)} />
            <Stat
              label="Alive"
              value={
                testAll.data
                  ? `${testAll.data.alive_count} / ${testAll.data.count}`
                  : "—"
              }
              tone={
                testAll.data && testAll.data.alive_count > 0
                  ? "text-status-success-fg"
                  : undefined
              }
            />
            <Stat label="This PC's IP" value={localIp || "—"} mono />
          </div>

          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {proxies.map((p) => (
              <ProxyCard
                key={p.id}
                proxy={p}
                result={results.get(p.id)}
                testing={testingId === p.id}
                localIp={localIp}
                onTest={() => void testOne(p.id)}
                onDelete={() => void deleteOne(p.id)}
              />
            ))}
          </div>
        </div>
      )}

      <AddProxiesDialog
        open={addOpen}
        onOpenChange={setAddOpen}
        onAdded={() => void qc.invalidateQueries({ queryKey: ["proxies"] })}
      />
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
      <span
        className={cn(
          "text-lg font-bold tracking-tight",
          mono && "num text-base",
          tone,
        )}
      >
        {value}
      </span>
    </div>
  )
}

function ProxyCard({
  proxy,
  result,
  testing,
  localIp,
  onTest,
  onDelete,
}: {
  proxy: ProxyRow
  result: ProxyResult | undefined
  testing: boolean
  localIp: string
  onTest: () => void
  onDelete: () => void
}) {
  const tested = !!result
  const alive = result?.alive ?? false
  const samePc = result?.differs_from_local === false
  const routesElsewhere = result?.differs_from_local === true

  async function copyLine() {
    try {
      const r = await api.get<ProxyLine>(
        `/proxies/${encodeURIComponent(proxy.id)}/line`,
      )
      await navigator.clipboard.writeText(r.line)
      toast.success("Copied full proxy line")
    } catch {
      toast.error("Could not copy proxy line")
    }
  }

  return (
    <div
      className={cn(
        "flex flex-col gap-3 border bg-card p-4",
        tested
          ? alive
            ? samePc
              ? "border-status-warning/60"
              : "border-status-success/50"
            : "border-primary/50"
          : "border-border",
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <SchemeBadge scheme={proxy.scheme} />
          <StatusDot tested={tested} alive={alive} pending={testing} />
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <Button
            variant="ghost"
            size="icon-sm"
            onClick={onTest}
            disabled={testing}
            aria-label="Test this proxy"
            title="Test this proxy"
          >
            {testing ? (
              <Loader2 className="animate-spin" />
            ) : (
              <Activity />
            )}
          </Button>
          <Button
            variant="ghost"
            size="icon-sm"
            onClick={onDelete}
            aria-label="Delete this proxy"
            title="Delete this proxy"
          >
            <Trash2 className="text-status-critical-fg" />
          </Button>
        </div>
      </div>

      {/* Per-field copy: host, port, username, + full line (incl. password). */}
      <div className="space-y-1 text-xs">
        <CopyRow label="Host" value={proxy.host} mono />
        <CopyRow label="Port" value={proxy.port} mono />
        <CopyRow label="User" value={proxy.label} mono />
        <div className="flex items-center gap-2">
          <span className="eyebrow w-12 shrink-0">Line</span>
          <button
            type="button"
            onClick={() => void copyLine()}
            className="truncate rounded px-1 py-0.5 text-left text-muted-foreground hover:bg-muted/60 hover:text-foreground"
            title="Copy the full proxy line (with password)"
          >
            copy full line (with password)
          </button>
        </div>
      </div>

      {/* Test results */}
      {testing ? (
        <div className="text-xs text-muted-foreground">Testing…</div>
      ) : !tested ? null : alive ? (
        <div className="space-y-1.5 border-t border-border pt-2">
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
            <div className="mt-1 border border-status-warning/40 bg-status-warning/10 px-2 py-1 text-[0.7rem] font-medium text-status-warning-fg">
              ⚠ Exit IP equals this PC's IP ({localIp}) — proxy may not be routing.
            </div>
          ) : routesElsewhere ? (
            <div className="mt-1 text-[0.7rem] text-status-success-fg">
              ✓ Routes through a different IP than this PC.
            </div>
          ) : null}
        </div>
      ) : (
        <div className="space-y-1 border-t border-border pt-2">
          <div className="text-xs font-semibold uppercase tracking-wide text-primary">
            Dead
          </div>
          {result?.error ? (
            <div className="break-words text-[0.7rem] text-muted-foreground">
              {result.error}
            </div>
          ) : null}
        </div>
      )}
    </div>
  )
}

function CopyRow({
  label,
  value,
  mono,
}: {
  label: string
  value: string
  mono?: boolean
}) {
  return (
    <div className="flex items-center gap-2">
      <span className="eyebrow w-12 shrink-0">{label}</span>
      <CopyValue value={value} label={label.toLowerCase()} mono={mono} />
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
      ? "bg-status-warning animate-pulse"
      : "bg-muted-foreground/50"
    : alive
      ? "bg-status-success"
      : "bg-primary"
  return (
    <span className="flex size-2.5 shrink-0">
      <span className={cn("inline-flex size-2.5 rounded-full", tone)} />
    </span>
  )
}

function Field({
  icon: Icon,
  label,
  children,
}: {
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

function AddProxiesDialog({
  open,
  onOpenChange,
  onAdded,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  onAdded: () => void
}) {
  const [text, setText] = useState("")
  const [result, setResult] = useState<AddResponse | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit() {
    if (!text.trim()) return
    setBusy(true)
    setResult(null)
    try {
      const res = await api.post<AddResponse>("/proxies", { text })
      setResult(res)
      if (res.added > 0) {
        toast.success(
          `Added ${res.added} prox${res.added === 1 ? "y" : "ies"}`,
        )
        onAdded()
        setText("")
      } else if (res.errors.length === 0) {
        toast.info("Nothing new — all already present")
      }
    } catch {
      toast.error("Could not add proxies")
    } finally {
      setBusy(false)
    }
  }

  function close(next: boolean) {
    onOpenChange(next)
    if (!next) {
      setText("")
      setResult(null)
    }
  }

  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Add proxies</DialogTitle>
          <DialogDescription>
            Paste one or many — any format: <span className="num">host:port:user:pass</span>,{" "}
            <span className="num">user:pass@host:port</span>, with or without{" "}
            <span className="num">http://</span> / <span className="num">socks5://</span>{" "}
            (no prefix = http). One per line.
          </DialogDescription>
        </DialogHeader>

        <Textarea
          rows={8}
          placeholder={
            "http://host:8080:user:pass\nsocks5://host:1080:user:pass\nuser:pass@host:8080"
          }
          value={text}
          onChange={(e) => setText(e.target.value)}
          className="num text-xs"
        />

        {result ? (
          <div className="space-y-1 text-xs">
            <p className="text-status-success-fg">
              Added {result.added} of {result.parsed} parsed.
            </p>
            {result.errors.length > 0 ? (
              <div className="border border-status-warning/40 bg-status-warning/10 px-2 py-1">
                <p className="font-semibold text-status-warning-fg">
                  {result.errors.length} line(s) couldn't be parsed:
                </p>
                <ul className="num mt-1 max-h-24 overflow-y-auto text-muted-foreground">
                  {result.errors.map((e, i) => (
                    <li key={i} className="truncate">
                      {e.line}
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </div>
        ) : null}

        <DialogFooter>
          <DialogClose render={<Button variant="outline" />}>Close</DialogClose>
          <Button onClick={() => void submit()} disabled={busy || !text.trim()}>
            {busy ? (
              <Loader2 data-icon="inline-start" className="animate-spin" />
            ) : (
              <Plus data-icon="inline-start" />
            )}
            Add
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
