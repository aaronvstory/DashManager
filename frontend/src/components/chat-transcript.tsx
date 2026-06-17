import { ArrowUpRight, MessageSquareDashed } from "lucide-react"
import type { ChatDirection } from "@/lib/types"
import { cn } from "@/lib/utils"

export interface TranscriptMessage {
  direction: ChatDirection
  content: string
  /** optional ISO timestamp (history view passes it; live view may not) */
  ts?: string
  /** render as a small centered escalation marker instead of a bubble */
  escalation?: boolean
}

function timeLabel(ts?: string): string | null {
  if (!ts) return null
  const date = new Date(ts)
  if (Number.isNaN(date.getTime())) return null
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
}

/**
 * Reusable support-chat transcript: out = right/primary-tinted, in =
 * left/muted, system = centered subtle italic, escalations = small centered
 * markers. Used by the live Run page and the History detail view.
 */
export function ChatTranscript({
  messages,
  className,
}: {
  messages: TranscriptMessage[]
  className?: string
}) {
  if (messages.length === 0) {
    return (
      <div
        className={cn(
          "flex flex-col items-center justify-center gap-2 py-12 text-center",
          className,
        )}
      >
        <MessageSquareDashed className="size-6 text-muted-foreground/60" />
        <p className="text-sm text-muted-foreground">No messages yet.</p>
      </div>
    )
  }

  return (
    <div className={cn("flex flex-col gap-2.5", className)}>
      {messages.map((m, i) => {
        const time = timeLabel(m.ts)

        if (m.escalation) {
          return (
            <div key={i} className="flex items-center gap-2 py-1">
              <span className="h-px flex-1 bg-border" />
              <span className="inline-flex items-center gap-1 rounded-full border border-status-warning/30 bg-status-warning/10 px-2 py-0.5 text-[11px] font-medium text-status-warning-fg">
                <ArrowUpRight className="size-3" />
                {m.content}
              </span>
              <span className="h-px flex-1 bg-border" />
            </div>
          )
        }

        if (m.direction === "system") {
          return (
            <div key={i} className="px-6 py-0.5 text-center">
              <span className="text-xs text-muted-foreground/80 italic">
                {m.content}
              </span>
            </div>
          )
        }

        const out = m.direction === "out"
        return (
          <div
            key={i}
            className={cn("flex flex-col gap-0.5", out ? "items-end" : "items-start")}
          >
            <div
              className={cn(
                "max-w-[85%] rounded-2xl px-3.5 py-2 text-sm leading-relaxed whitespace-pre-wrap",
                out
                  ? "rounded-br-sm border border-primary/20 bg-primary/10 text-foreground"
                  : "rounded-bl-sm bg-muted text-foreground",
              )}
            >
              {m.content}
            </div>
            <span className="px-1 text-[10px] text-muted-foreground/70">
              {out ? "You" : "Support"}
              {time ? ` · ${time}` : ""}
            </span>
          </div>
        )
      })}
    </div>
  )
}
