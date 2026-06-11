import { cn } from "@/lib/utils"

/** Dasher-ish glyph: two offset speed dashes on a DoorDash-red tile. */
export function DashGlyph({ className }: { className?: string }) {
  return (
    <span
      className={cn(
        "flex size-8 shrink-0 items-center justify-center rounded-lg bg-primary shadow-[0_2px_10px_-2px_rgba(235,23,0,0.55)]",
        className,
      )}
    >
      <svg viewBox="0 0 32 32" className="size-5" fill="#ffffff" aria-hidden="true">
        <rect x="5" y="9" width="18" height="5.5" rx="2.75" />
        <rect x="10" y="18" width="16" height="5.5" rx="2.75" />
      </svg>
    </span>
  )
}

export function Logo() {
  return (
    <div className="flex items-center gap-2.5">
      <DashGlyph />
      <span className="text-[15px] font-semibold tracking-tight text-foreground">
        Dash<span className="text-primary">Manager</span>
      </span>
    </div>
  )
}
