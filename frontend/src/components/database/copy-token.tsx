import { useState } from "react"
import { Check, Copy } from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

/**
 * Monospace token chip with a copy button. Used for number tokens and order
 * UUIDs in the Database viewer — long values are truncated visually but the
 * full value is copied.
 */
export function CopyToken({
  value,
  truncate = 12,
  label = "value",
  className,
}: {
  value: string
  /** Show this many leading characters before the ellipsis. */
  truncate?: number
  label?: string
  className?: string
}) {
  const [copied, setCopied] = useState(false)
  const display =
    value.length > truncate ? `${value.slice(0, truncate)}…` : value

  async function copy() {
    try {
      await navigator.clipboard.writeText(value)
      setCopied(true)
      toast.success(`Copied ${label}`)
      window.setTimeout(() => setCopied(false), 1200)
    } catch {
      toast.error("Could not copy to clipboard")
    }
  }

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-md border border-border/60 bg-muted/40 py-0.5 pr-0.5 pl-2 font-mono text-[11px] text-foreground/80",
        className,
      )}
    >
      <span title={value}>{display}</span>
      <Button
        variant="ghost"
        size="icon-sm"
        className="size-5"
        onClick={() => void copy()}
        aria-label={`Copy ${label}`}
      >
        {copied ? (
          <Check className="size-3 text-emerald-500" />
        ) : (
          <Copy className="size-3" />
        )}
      </Button>
    </span>
  )
}
