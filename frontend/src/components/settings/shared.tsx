/**
 * Shared building blocks for the Settings page.
 *
 * Backend contract (backend/routes/settings.py + backend/config.py):
 * - GET  /api/settings           -> the full settings map (DEFAULT_SETTINGS shape)
 * - PUT  /api/settings/{key}     -> body IS the raw JSON value (no envelope) and
 *                                   replaces that key wholesale, so each section
 *                                   always saves its FULL object.
 */
import type { ReactNode } from "react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import type { LucideIcon } from "lucide-react"
import { CircleCheck, LoaderCircle, Save } from "lucide-react"
import { toast } from "sonner"
import { ApiError } from "@/lib/api"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Label } from "@/components/ui/label"
import { cn } from "@/lib/utils"

// ---------------------------------------------------------------------------
// Types — mirror DEFAULT_SETTINGS in backend/config.py
// ---------------------------------------------------------------------------

export interface IdentityCaptureSettings {
  url: string
  labels: {
    first_name: string
    last_name: string
    email: string
    phone: string
  }
}

export interface RefundSignalSettings {
  total_label: string
  refund_label: string
  cancelled_texts: string[]
}

export interface ChatSettings {
  opening_template: string
  agent_word: string
  scripted_followups: string[]
  bot_patterns: string[]
  max_escalations: number
  success_phrases: string[]
  max_turns: number
  max_chat_seconds: number
}

export interface LlmSettings {
  model: string
  system_prompt: string
  max_turns: number
}

export interface BrowserSettings {
  headless: boolean
  viewport: number[]
  /** How many customer browsers run at once. */
  max_concurrent: number
}

export interface AppSettings {
  identity_capture: IdentityCaptureSettings
  refund_signal: RefundSignalSettings
  chat: ChatSettings
  llm: LlmSettings
  browser: BrowserSettings
  openrouter_api_key: string
}

export const SETTINGS_QUERY_KEY = ["settings"] as const

// ---------------------------------------------------------------------------
// Saving
// ---------------------------------------------------------------------------

/** PUT /api/settings/{key} — the body is the raw JSON value, no envelope. */
async function putSetting(key: string, value: unknown): Promise<void> {
  const res = await fetch(`/api/settings/${key}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(value),
  })
  if (!res.ok) {
    throw new ApiError(res.status, res.statusText, await res.text().catch(() => ""))
  }
}

export interface SettingEntry {
  key: string
  value: unknown
}

/** One mutation per section: write each entry, refresh the cache, toast. */
export function useSaveSettings(sectionLabel: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (entries: SettingEntry[]) => {
      for (const { key, value } of entries) await putSetting(key, value)
    },
    onSuccess: async () => {
      toast.success(`${sectionLabel} settings saved`)
      await queryClient.invalidateQueries({ queryKey: SETTINGS_QUERY_KEY })
    },
    onError: (err) => {
      toast.error(
        err instanceof Error ? err.message : `Could not save ${sectionLabel} settings`,
      )
    },
  })
}

// ---------------------------------------------------------------------------
// Field value helpers
// ---------------------------------------------------------------------------

export function listToLines(list: string[]): string {
  return list.join("\n")
}

/** Split on newlines, trim each line, drop empties — what the backend expects. */
export function linesToList(text: string): string[] {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
}

/** Strictly positive integer, or null when invalid. */
export function parsePositiveInt(raw: string): number | null {
  const trimmed = raw.trim()
  if (!/^\d+$/.test(trimmed)) return null
  const n = Number(trimmed)
  return n > 0 ? n : null
}

// ---------------------------------------------------------------------------
// Presentation
// ---------------------------------------------------------------------------

/** Inline code chip for placeholder tokens in helper text. */
export function Token({ children }: { children: ReactNode }) {
  return (
    <code className="rounded bg-muted px-1 py-px font-mono text-[11px] text-foreground/80">
      {children}
    </code>
  )
}

/** Label + control + optional helper line. */
export function Field({
  label,
  htmlFor,
  helper,
  className,
  children,
}: {
  label: string
  htmlFor: string
  helper?: ReactNode
  className?: string
  children: ReactNode
}) {
  return (
    <div className={cn("space-y-2", className)}>
      <Label htmlFor={htmlFor}>{label}</Label>
      {children}
      {helper ? (
        <p className="text-xs leading-relaxed text-muted-foreground">{helper}</p>
      ) : null}
    </div>
  )
}

/**
 * Card shell shared by every settings section: tinted icon, title/description
 * header, content slot, and a footer with dirty indicator + Save button.
 */
export function SettingsCard({
  icon: Icon,
  title,
  description,
  dirty,
  saving,
  invalid = false,
  onSave,
  footerExtra,
  children,
}: {
  icon: LucideIcon
  title: string
  description: ReactNode
  dirty: boolean
  saving: boolean
  /** True when a field fails validation — blocks saving. */
  invalid?: boolean
  onSave: () => void
  /** Extra footer action, e.g. the OpenRouter "Test key" button. */
  footerExtra?: ReactNode
  children: ReactNode
}) {
  return (
    <Card className="shadow-sm">
      <CardHeader className="border-b">
        <div className="flex items-center gap-3.5">
          <div className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-primary/10 ring-1 ring-primary/15">
            <Icon className="size-4 text-primary" />
          </div>
          <div className="space-y-1">
            <CardTitle>{title}</CardTitle>
            <CardDescription>{description}</CardDescription>
          </div>
        </div>
      </CardHeader>

      <CardContent className="space-y-5">{children}</CardContent>

      <CardFooter className="justify-between gap-3">
        {dirty ? (
          <span
            className={cn(
              "flex items-center gap-1.5 text-xs font-medium",
              invalid
                ? "text-destructive"
                : "text-amber-600 dark:text-amber-400",
            )}
          >
            <span
              className={cn(
                "size-1.5 rounded-full",
                invalid ? "bg-destructive" : "bg-amber-500",
              )}
            />
            {invalid ? "Fix invalid values to save" : "Unsaved changes"}
          </span>
        ) : (
          <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <CircleCheck className="size-3.5 text-emerald-600 dark:text-emerald-500" />
            Saved
          </span>
        )}
        <div className="flex items-center gap-2">
          {footerExtra}
          <Button size="sm" onClick={onSave} disabled={!dirty || invalid || saving}>
            {saving ? <LoaderCircle className="animate-spin" /> : <Save />}
            Save
          </Button>
        </div>
      </CardFooter>
    </Card>
  )
}
