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
  }
  const [form, setForm] = useState(initial)
  const [baseline, setBaseline] = useState(initial)

  const parsedWidth = parsePositiveInt(form.width)
  const parsedHeight = parsePositiveInt(form.height)
  const dirty = JSON.stringify(form) !== JSON.stringify(baseline)
  const invalid = parsedWidth === null || parsedHeight === null
  const save = useSaveSettings("Browser")

  function handleSave() {
    if (parsedWidth === null || parsedHeight === null) return
    save.mutate(
      [
        {
          key: "browser",
          value: {
            ...browser,
            headless: form.headless,
            viewport: [parsedWidth, parsedHeight],
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
            Headed is strongly recommended — DoorDash blocks headless browsers.
          </p>
          {form.headless ? (
            <p className="flex items-center gap-1.5 text-xs font-medium text-amber-600 dark:text-amber-400">
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
    </SettingsCard>
  )
}
