import { CheckCircle2, TriangleAlert, UserRound } from "lucide-react"
import { ScrollArea, ScrollBar } from "@/components/ui/scroll-area"
import { useRunStore } from "@/store/runStore"
import { cn } from "@/lib/utils"

/**
 * Horizontal strip of per-customer chips: position/total, name, done check,
 * session-invalid warning, and a pulsing dot on the customer in flight.
 */
export function CustomerProgressStrip() {
  const customers = useRunStore((s) => s.customersProgress)
  const runActive = useRunStore((s) => s.runActive)

  if (customers.length === 0) {
    return (
      <div className="flex items-center gap-2 rounded-xl border border-dashed border-border px-4 py-3 text-sm text-muted-foreground">
        <UserRound className="size-4" />
        Waiting for the first customer…
      </div>
    )
  }

  const total = customers[0]?.total ?? customers.length
  const doneCount = customers.filter((c) => c.done).length

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between text-xs text-muted-foreground">
        <span className="font-medium tracking-wide uppercase">Customers</span>
        <span className="tabular-nums">
          {doneCount}/{total} done
        </span>
      </div>
      <ScrollArea className="w-full">
        <div className="flex w-max gap-2 pb-2">
          {customers.map((c) => {
            const current = runActive && !c.done
            return (
              <div
                key={c.id}
                className={cn(
                  "flex shrink-0 items-center gap-2 rounded-lg border px-3 py-2 text-sm transition-colors",
                  c.sessionInvalid
                    ? "border-status-critical/30 bg-status-critical/5"
                    : c.done
                      ? "border-status-success/25 bg-status-success/5"
                      : current
                        ? "border-primary/30 bg-primary/5"
                        : "border-border bg-card",
                )}
              >
                <span className="text-xs text-muted-foreground tabular-nums">
                  {c.position}/{c.total}
                </span>
                <span className="max-w-40 truncate font-medium">{c.name}</span>
                {c.sessionInvalid ? (
                  <span
                    className="inline-flex items-center gap-1 text-xs text-status-critical-fg"
                    title="Session expired — customer skipped"
                  >
                    <TriangleAlert className="size-3.5" />
                    session
                  </span>
                ) : c.done ? (
                  <CheckCircle2 className="size-4 text-status-success-fg" />
                ) : current ? (
                  <span className="relative flex size-2">
                    <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-primary/70" />
                    <span className="relative inline-flex size-2 rounded-full bg-primary" />
                  </span>
                ) : null}
              </div>
            )
          })}
        </div>
        <ScrollBar orientation="horizontal" />
      </ScrollArea>
    </div>
  )
}
