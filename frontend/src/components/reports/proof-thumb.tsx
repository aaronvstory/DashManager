/**
 * A proof-screenshot thumbnail that expands on HOVER — no click, no new window.
 * Hovering shows a large floating preview anchored near the cursor; moving off
 * hides it. Brutalist: hard-edged thumb, hard-edged popover, hairline border.
 */
import { useState } from "react"
import type { ProofShot } from "@/lib/types"

export function ProofThumb({ shot }: { shot: ProofShot }) {
  const [hover, setHover] = useState(false)
  const [pos, setPos] = useState<{ x: number; y: number }>({ x: 0, y: 0 })

  return (
    <span
      className="relative inline-block"
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      onMouseMove={(e) => setPos({ x: e.clientX, y: e.clientY })}
    >
      <span className="block w-[120px] cursor-zoom-in border border-border bg-muted/40">
        <img
          src={shot.url}
          alt={shot.label}
          loading="lazy"
          className="h-[78px] w-full object-cover object-top"
        />
        <span className="block truncate border-t border-border px-1.5 py-1 text-[0.62rem] uppercase tracking-wide text-muted-foreground">
          {shot.label || shot.kind}
        </span>
      </span>

      {hover ? (
        <span
          className="pointer-events-none fixed z-50 border-2 border-primary bg-background shadow-[6px_6px_0_0_rgba(0,0,0,0.5)]"
          style={{
            // Anchor the big preview near the cursor, clamped to the viewport.
            left: Math.min(pos.x + 24, window.innerWidth - 740),
            top: Math.max(12, Math.min(pos.y - 200, window.innerHeight - 560)),
            width: 720,
          }}
        >
          <img
            src={shot.url}
            alt={shot.label}
            className="block max-h-[540px] w-full object-contain"
          />
          <span className="block border-t-2 border-primary px-3 py-1.5 text-xs uppercase tracking-wide text-foreground">
            {shot.label || shot.kind}
          </span>
        </span>
      ) : null}
    </span>
  )
}
