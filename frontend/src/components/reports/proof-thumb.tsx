/**
 * A proof-screenshot thumbnail that expands on HOVER — no click, no new window.
 * Hovering shows a large floating preview anchored near the cursor; moving off
 * hides it. Brutalist: hard-edged thumb, hard-edged popover, hairline border.
 */
import { useState } from "react"
import type { ProofShot } from "@/lib/types"

interface HoverState {
  x: number
  y: number
  vw: number
  vh: number
}

export function ProofThumb({ shot }: { shot: ProofShot }) {
  // Captured ONCE on enter (cursor + viewport dims) so we don't re-render on
  // every mousemove AND the clamping uses fresh dimensions even after a resize.
  const [hover, setHover] = useState<HoverState | null>(null)

  const previewW = hover ? Math.min(720, Math.max(280, hover.vw - 48)) : 720
  const previewH = hover ? Math.min(540, hover.vh - 48) : 540

  return (
    <span
      className="relative inline-block"
      onMouseEnter={(e) =>
        setHover({
          x: e.clientX,
          y: e.clientY,
          vw: window.innerWidth,
          vh: window.innerHeight,
        })
      }
      onMouseLeave={() => setHover(null)}
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
            // Anchor near the cursor, clamped to the viewport on both axes.
            left: Math.max(12, Math.min(hover.x + 24, hover.vw - previewW - 12)),
            top: Math.max(12, Math.min(hover.y - 200, hover.vh - previewH - 40)),
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
