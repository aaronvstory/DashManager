/**
 * A proof-screenshot thumbnail that expands on HOVER — no click, no new window.
 * Hovering shows a large floating preview anchored near the cursor; moving off
 * hides it. Brutalist: hard-edged thumb, hard-edged popover, hairline border.
 */
import { useState } from "react"
import type { ProofShot } from "@/lib/types"

export function ProofThumb({ shot }: { shot: ProofShot }) {
  const [pos, setPos] = useState<{ x: number; y: number } | null>(null)

  // Preview width adapts to the viewport so it never overflows on narrow screens.
  const vw = typeof window !== "undefined" ? window.innerWidth : 1280
  const vh = typeof window !== "undefined" ? window.innerHeight : 800
  const previewW = Math.min(720, Math.max(280, vw - 48))
  const previewH = Math.min(540, vh - 48)

  return (
    <span
      className="relative inline-block"
      // Position is set ONCE on enter (not on every mousemove) — avoids
      // high-frequency re-renders; the small thumb doesn't need cursor tracking.
      onMouseEnter={(e) => setPos({ x: e.clientX, y: e.clientY })}
      onMouseLeave={() => setPos(null)}
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

      {pos ? (
        <span
          className="pointer-events-none fixed z-50 border-2 border-primary bg-background shadow-[6px_6px_0_0_rgba(0,0,0,0.5)]"
          style={{
            // Anchor near the cursor, clamped to the viewport on both axes.
            left: Math.max(12, Math.min(pos.x + 24, vw - previewW - 12)),
            top: Math.max(12, Math.min(pos.y - 200, vh - previewH - 40)),
            width: previewW,
          }}
        >
          <img
            src={shot.url}
            alt={shot.label}
            className="block w-full object-contain"
            style={{ maxHeight: previewH }}
          />
          <span className="block border-t-2 border-primary px-3 py-1.5 text-xs uppercase tracking-wide text-foreground">
            {shot.label || shot.kind}
          </span>
        </span>
      ) : null}
    </span>
  )
}
