import { useState } from "react"
import { Monitor, TriangleAlert } from "lucide-react"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import {
  Field,
  parsePositiveInt,
  SettingsCard,
  useSaveSettings,
  type BrowserSettings,
} from "@/components/settings/shared"

/** Playwright launch options — the 'browser' settings object. */
export function BrowserSection({ browser }: { browser: BrowserSettings }) {
  const initial = {
    headless: browser.headless,
    width: String(browser.viewport[0] ?? 1400),
    height: String(browser.viewport[1] ?? 900),
    maxConcurrent: String(browser.max_concurrent ?? 1),
  }
  const [form, setForm] = useState(initial)
  const [baseline, setBaseline] = useState(initial)

  const parsedWidth = parsePositiveInt(form.width)
  const parsedHeight = parsePositiveInt(form.height)
  const parsedMaxConcurrent = parsePositiveInt(form.maxConcurrent)
  const dirty = JSON.stringify(form) !== JSON.stringify(baseline)
  const invalid =
    parsedWidth === null || parsedHeight === null || parsedMaxConcurrent === null
  const save = useSaveSettings("Browser")

  function handleSave() {
    if (
      parsedWidth === null ||
      parsedHeight === null ||
      parsedMaxConcurrent === null
    )
      return
    save.mutate(
      [
        {
          key: "browser",
          value: {
            ...browser,
            headless: form.headless,
            viewport: [parsedWidth, parsedHeight],
            max_concurrent: parsedMaxConcurrent,
          },
        },
      ],
      { onSuccess: () => setBaseline(form) },
    )
  }

  return (
    <SettingsCard
      icon={Monitor}
      title="Browser"
      description="How the Chromium instance is launched for logins, scraping, and chats."
      dirty={dirty}
      saving={save.isPending}
      invalid={invalid}
      onSave={handleSave}
    >
      <div className="flex items-start justify-between gap-4 rounded-lg border border-border/60 bg-muted/30 p-4">
        <div className="space-y-1.5">
          <Label htmlFor="browser-headless">Headless mode</Label>
          <p className="text-xs leading-relaxed text-muted-foreground">
            Headed (off) is strongly recommended — DoorDash blocks headless. This
            is the default for all actions.
          </p>
          {form.headless ? (
            <p className="flex items-center gap-1.5 text-xs font-medium text-status-warning-fg">
              <TriangleAlert className="size-3.5 shrink-0" />
              Headless runs are very likely to be blocked.
            </p>
          ) : null}
        </div>
        <Switch
          id="browser-headless"
          checked={form.headless}
          onCheckedChange={(checked) =>
            setForm((f) => ({ ...f, headless: checked }))
          }
        />
      </div>

      <div className="grid gap-5 sm:grid-cols-2">
        <Field label="Viewport width (px)" htmlFor="browser-viewport-width">
          <Input
            id="browser-viewport-width"
            type="number"
            min={1}
            value={form.width}
            onChange={(e) => setForm((f) => ({ ...f, width: e.target.value }))}
            aria-invalid={parsedWidth === null}
          />
        </Field>
        <Field label="Viewport height (px)" htmlFor="browser-viewport-height">
          <Input
            id="browser-viewport-height"
            type="number"
            min={1}
            value={form.height}
            onChange={(e) => setForm((f) => ({ ...f, height: e.target.value }))}
            aria-invalid={parsedHeight === null}
          />
        </Field>
      </div>

      <Field
        label="Max concurrent browsers"
        htmlFor="browser-max-concurrent"
        helper="How many customer browsers run at once."
      >
        <Input
          id="browser-max-concurrent"
          type="number"
          min={1}
          value={form.maxConcurrent}
          onChange={(e) =>
            setForm((f) => ({ ...f, maxConcurrent: e.target.value }))
          }
          aria-invalid={parsedMaxConcurrent === null}
        />
      </Field>
    </SettingsCard>
  )
}
