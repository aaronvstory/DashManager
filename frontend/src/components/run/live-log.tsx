import { useEffect, useRef, useState } from "react"
import { ChevronDown, Terminal } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Card } from "@/components/ui/card"
import { ScrollArea } from "@/components/ui/scroll-area"
import { useRunStore } from "@/store/runStore"
import type { AppEvent } from "@/lib/types"
import { cn } from "@/lib/utils"

function levelOf(ev: AppEvent): "error" | "warn" | "info" {
  if (ev.type === "run_error" || ev.type === "login_failed") return "error"
  if (ev.type === "session_invalid") return "warn"
  if (ev.type === "log") {
    const level = String(ev.data.level ?? "info")
    if (level === "error") return "error"
    if (level === "warning" || level === "warn") return "warn"
  }
  return "info"
}

const LEVEL_CLASS: Record<"error" | "warn" | "info", string> = {
  error: "text-status-critical-fg",
  warn: "text-status-warning-fg",
  info: "text-muted-foreground",
}

function lineFor(ev: AppEvent): string {
  if (ev.type === "log") {
    return String(ev.data.message ?? ev.data.msg ?? "")
  }
  const detail = Object.entries(ev.data)
    .filter(([, v]) => typeof v !== "object" || v === null)
    .map(([k, v]) => `${k}=${String(v)}`)
    .join(" ")
  return detail ? `${ev.type}  ${detail}` : ev.type
}

function timeOf(ev: AppEvent): string {
  const date = new Date(ev.ts)
  if (Number.isNaN(date.getTime())) return ""
  return date.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  })
}

/** Collapsible monospace event log; auto-follows the tail while expanded. */
export function LiveLog() {
  const liveLog = useRunStore((s) => s.liveLog)
  const [open, setOpen] = useState(false)
  const tailRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (open) tailRef.current?.scrollIntoView({ block: "nearest" })
  }, [open, liveLog.length])

  return (
    <Card size="sm" className="gap-0 py-0">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-4 py-3 text-left text-sm font-medium transition-colors hover:bg-muted/40"
        aria-expanded={open}
      >
        <Terminal className="size-4 text-muted-foreground" />
        Live log
        <Badge variant="secondary" className="tabular-nums">
          {liveLog.length}
        </Badge>
        <ChevronDown
          className={cn(
            "ml-auto size-4 text-muted-foreground transition-transform",
            open && "rotate-180",
          )}
        />
      </button>
      {open ? (
        <ScrollArea className="h-64 border-t">
          <div className="space-y-0.5 p-3 font-mono text-xs leading-relaxed">
            {liveLog.length === 0 ? (
              <p className="text-muted-foreground/70">No events yet.</p>
            ) : (
              liveLog.map((ev, i) => {
                const level = levelOf(ev)
                return (
                  <div key={`${ev.id}-${i}`} className="flex gap-2">
                    <span className="shrink-0 text-muted-foreground/50 tabular-nums">
                      {timeOf(ev)}
                    </span>
                    <span className={cn("break-all", LEVEL_CLASS[level])}>
                      {lineFor(ev)}
                    </span>
                  </div>
                )
              })
            )}
            <div ref={tailRef} />
          </div>
        </ScrollArea>
      ) : null}
    </Card>
  )
}
