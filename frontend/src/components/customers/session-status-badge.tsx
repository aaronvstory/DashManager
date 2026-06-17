import { Badge } from "@/components/ui/badge"
import type { SessionStatus } from "@/lib/types"
import { DOT, TONE, type Tone } from "@/lib/status-tone"
import { cn } from "@/lib/utils"

const STYLES: Record<SessionStatus, { label: string; tone: Tone }> = {
  active: { label: "Active", tone: "success" },
  expired: { label: "Expired", tone: "warning" },
  invalid: { label: "Invalid", tone: "critical" },
}

export function SessionStatusBadge({ status }: { status: SessionStatus }) {
  const s = STYLES[status] ?? STYLES.invalid
  return (
    <Badge variant="outline" className={cn("gap-1.5", TONE[s.tone])}>
      <span
        className={cn("size-1.5 shrink-0 rounded-full", DOT[s.tone])}
        aria-hidden
      />
      {s.label}
    </Badge>
  )
}
