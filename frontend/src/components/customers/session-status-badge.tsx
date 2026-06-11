import { Badge } from "@/components/ui/badge"
import type { SessionStatus } from "@/lib/types"
import { cn } from "@/lib/utils"

const STYLES: Record<SessionStatus, { label: string; badge: string; dot: string }> = {
  active: {
    label: "Active",
    badge: "border-emerald-500/25 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
    dot: "bg-emerald-500",
  },
  expired: {
    label: "Expired",
    badge: "border-amber-500/25 bg-amber-500/10 text-amber-600 dark:text-amber-400",
    dot: "bg-amber-500",
  },
  invalid: {
    label: "Invalid",
    badge: "border-red-500/25 bg-red-500/10 text-red-600 dark:text-red-400",
    dot: "bg-red-500",
  },
}

export function SessionStatusBadge({ status }: { status: SessionStatus }) {
  const s = STYLES[status] ?? STYLES.invalid
  return (
    <Badge variant="outline" className={cn("gap-1.5", s.badge)}>
      <span className={cn("size-1.5 shrink-0 rounded-full", s.dot)} aria-hidden />
      {s.label}
    </Badge>
  )
}
