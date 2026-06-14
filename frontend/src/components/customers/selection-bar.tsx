import { LoaderCircle, Play, X } from "lucide-react"
import { Button } from "@/components/ui/button"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Separator } from "@/components/ui/separator"
import type { StrategyName } from "@/lib/types"
import { STRATEGY_ITEMS } from "./helpers"

export function SelectionBar({
  count,
  strategy,
  starting,
  onStrategyChange,
  onRun,
  onClear,
}: {
  count: number
  strategy: StrategyName
  starting: boolean
  onStrategyChange: (s: StrategyName) => void
  onRun: () => void
  onClear: () => void
}) {
  return (
    <div className="fixed bottom-6 left-[calc(50%+7.5rem)] z-40 -translate-x-1/2 animate-in fade-in-0 slide-in-from-bottom-4">
      <div className="flex items-center gap-3 border-2 border-primary bg-card py-2.5 pr-2.5 pl-4 shadow-[6px_6px_0_0_rgba(0,0,0,0.5)]">
        <div className="flex items-center gap-2 text-sm font-medium">
          <span className="flex size-6 items-center justify-center rounded-full bg-primary text-xs font-semibold text-primary-foreground">
            {count}
          </span>
          selected
        </div>

        <Separator orientation="vertical" className="h-6!" />

        <div className="flex items-center gap-2">
          <span className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
            Strategy
          </span>
          <Select
            items={STRATEGY_ITEMS}
            value={strategy}
            onValueChange={(v) => {
              if (v) onStrategyChange(v as StrategyName)
            }}
          >
            <SelectTrigger size="sm" className="min-w-32">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {STRATEGY_ITEMS.map((it) => (
                <SelectItem key={it.value} value={it.value}>
                  {it.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <Button size="sm" onClick={onRun} disabled={starting}>
          {starting ? (
            <LoaderCircle data-icon="inline-start" className="animate-spin" />
          ) : (
            <Play data-icon="inline-start" />
          )}
          Run selected
        </Button>

        <Button
          variant="ghost"
          size="icon-sm"
          onClick={onClear}
          aria-label="Clear selection"
        >
          <X />
        </Button>
      </div>
    </div>
  )
}
