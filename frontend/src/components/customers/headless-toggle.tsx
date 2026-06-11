import { useEffect, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Eye, EyeOff } from "lucide-react"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { api } from "@/lib/api"
import {
  SETTINGS_QUERY_KEY,
  type AppSettings,
} from "@/components/settings/shared"
import { cn } from "@/lib/utils"

/**
 * Per-action "Show browser window" control.
 *
 * Defaults, in order: the user's last per-action pick (localStorage) → the
 * global browser.headless setting → headed. Flipping it sends an explicit
 * `headless` in the action's POST body, overriding the global default for that
 * one action. The choice is persisted so it sticks across dialog opens.
 *
 * The control is phrased as "Show browser window" (headed) because that is what
 * the user experiences — a real Chromium window they can watch. `headless` is
 * the inverse.
 */

const STORAGE_KEY = "dashmanager.headless-override"

function readStored(): boolean | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw === "true") return true
    if (raw === "false") return false
  } catch {
    // ignore — storage unavailable
  }
  return null
}

function writeStored(value: boolean): void {
  try {
    localStorage.setItem(STORAGE_KEY, value ? "true" : "false")
  } catch {
    // ignore — storage unavailable
  }
}

/**
 * Resolve the effective headless value for an action.
 *
 * `value` is the controlled headless state from useHeadlessOverride. Returns
 * `undefined` while the global default is still loading so the caller can omit
 * the field (and let the backend apply the global setting) rather than guess.
 */

export function useHeadlessOverride(): {
  /** Current headless value, or null until the global default resolves. */
  headless: boolean | null
  /** What to send in the POST body (omit the field when null). */
  setHeadless: (next: boolean) => void
  /** True while the global default is being fetched and no stored pick exists. */
  loading: boolean
} {
  const settingsQuery = useQuery({
    queryKey: SETTINGS_QUERY_KEY,
    queryFn: () => api.get<AppSettings>("/settings"),
    staleTime: 30_000,
  })

  const stored = readStored()
  const [headless, setHeadlessState] = useState<boolean | null>(stored)

  // Once the global default arrives, seed the control if the user has no pick.
  const globalHeadless = settingsQuery.data?.browser.headless ?? null
  useEffect(() => {
    if (headless === null && globalHeadless !== null) {
      setHeadlessState(globalHeadless)
    }
  }, [headless, globalHeadless])

  function setHeadless(next: boolean) {
    setHeadlessState(next)
    writeStored(next)
  }

  return {
    headless,
    setHeadless,
    loading: headless === null && settingsQuery.isLoading,
  }
}

export function HeadlessToggle({
  headless,
  onChange,
  className,
}: {
  /** Null is treated as headed (the safe default) for display. */
  headless: boolean | null
  onChange: (next: boolean) => void
  className?: string
}) {
  // "Show browser window" is ON when NOT headless.
  const showWindow = headless !== true

  return (
    <div
      className={cn(
        "flex items-center justify-between gap-3 rounded-lg border border-border/60 bg-muted/30 px-3 py-2.5",
        className,
      )}
    >
      <div className="flex items-center gap-2.5">
        {showWindow ? (
          <Eye className="size-4 shrink-0 text-primary" />
        ) : (
          <EyeOff className="size-4 shrink-0 text-muted-foreground" />
        )}
        <div className="space-y-0.5">
          <Label htmlFor="headless-toggle" className="cursor-pointer">
            Show browser window
          </Label>
          <p className="text-xs text-muted-foreground">
            {showWindow
              ? "You'll see the Chromium window as it works."
              : "Headless — likely to be blocked by DoorDash."}
          </p>
        </div>
      </div>
      <Switch
        id="headless-toggle"
        checked={showWindow}
        onCheckedChange={(checked) => onChange(!checked)}
      />
    </div>
  )
}
