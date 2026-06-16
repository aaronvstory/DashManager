import { Check, Copy } from "lucide-react"
import { useState } from "react"
import { toast } from "sonner"
import { cn } from "@/lib/utils"

/**
 * A one-click-copyable value (the create-modal results table's core win over
 * the CustomerDaisy terminal). Click anywhere on the cell to copy; a brief
 * check mark confirms. Empty values render a muted dash and aren't clickable.
 */
export function CopyValue({
  value,
  label,
  className,
  mono,
}: {
  value: string
  /** What was copied, for the toast — defaults to the value itself. */
  label?: string
  className?: string
  /** Tabular data (email/phone/id) reads better monospaced. */
  mono?: boolean
}) {
  const [copied, setCopied] = useState(false)

  async function copy() {
    if (!value) return
    try {
      await navigator.clipboard.writeText(value)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1200)
      toast.success(`Copied ${label ?? value}`)
    } catch {
      toast.error("Could not copy")
    }
  }

  if (!value) {
    return <span className="text-muted-foreground">—</span>
  }

  return (
    <button
      type="button"
      onClick={() => void copy()}
      title={`Copy ${label ?? value}`}
      className={cn(
        "group inline-flex max-w-full items-center gap-1.5 rounded px-1 py-0.5 text-left",
        "hover:bg-muted/60 focus-visible:bg-muted/60 focus-visible:outline-none",
        mono && "num",
        className,
      )}
    >
      <span className="truncate">{value}</span>
      {copied ? (
        <Check className="size-3 shrink-0 text-emerald-500" />
      ) : (
        <Copy className="size-3 shrink-0 text-muted-foreground/40 transition-colors group-hover:text-muted-foreground" />
      )}
    </button>
  )
}
