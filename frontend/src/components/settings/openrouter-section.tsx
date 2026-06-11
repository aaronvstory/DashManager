import { useState } from "react"
import { useMutation } from "@tanstack/react-query"
import { Eye, EyeOff, KeyRound, LoaderCircle, PlugZap } from "lucide-react"
import { toast } from "sonner"
import { api } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Field,
  SettingsCard,
  useSaveSettings,
  type LlmSettings,
  type SettingEntry,
} from "@/components/settings/shared"

/**
 * OpenRouter credentials. Saves TWO settings keys: the raw
 * 'openrouter_api_key' string, and 'llm' (model lives inside that object).
 */
export function OpenRouterSection({
  apiKey,
  llm,
}: {
  apiKey: string
  llm: LlmSettings
}) {
  const [key, setKey] = useState(apiKey)
  const [model, setModel] = useState(llm.model)
  const [showKey, setShowKey] = useState(false)
  const [baseline, setBaseline] = useState({ key: apiKey, model: llm.model })

  const dirty = key !== baseline.key || model !== baseline.model
  const invalid = model.trim() === ""
  const save = useSaveSettings("OpenRouter")

  const testKey = useMutation({
    mutationFn: () =>
      api.post<{ ok: boolean; message: string }>("/settings/test-llm-key"),
    onSuccess: (res) => {
      if (res.ok) toast.success(res.message || "OpenRouter key works")
      else toast.error(res.message || "OpenRouter key test failed")
    },
    onError: (err) =>
      toast.error(err instanceof Error ? err.message : "OpenRouter key test failed"),
  })

  function handleSave() {
    const nextKey = key.trim()
    const nextModel = model.trim()
    const entries: SettingEntry[] = []
    if (nextKey !== baseline.key)
      entries.push({ key: "openrouter_api_key", value: nextKey })
    if (nextModel !== baseline.model)
      entries.push({ key: "llm", value: { ...llm, model: nextModel } })
    save.mutate(entries, {
      onSuccess: () => {
        setKey(nextKey)
        setModel(nextModel)
        setBaseline({ key: nextKey, model: nextModel })
      },
    })
  }

  return (
    <SettingsCard
      icon={KeyRound}
      title="OpenRouter"
      description="API access for the LLM chat strategy. The key test runs against the last saved key and model."
      dirty={dirty}
      saving={save.isPending}
      invalid={invalid}
      onSave={handleSave}
      footerExtra={
        <Button
          variant="outline"
          size="sm"
          onClick={() => testKey.mutate()}
          disabled={testKey.isPending}
        >
          {testKey.isPending ? <LoaderCircle className="animate-spin" /> : <PlugZap />}
          Test key
        </Button>
      }
    >
      <Field
        label="API key"
        htmlFor="openrouter-key"
        helper="Leave empty to use the OPENROUTER_API_KEY environment variable."
      >
        <div className="relative">
          <Input
            id="openrouter-key"
            type={showKey ? "text" : "password"}
            value={key}
            onChange={(e) => setKey(e.target.value)}
            placeholder="sk-or-v1-…"
            autoComplete="off"
            spellCheck={false}
            className="pr-9"
          />
          <button
            type="button"
            onClick={() => setShowKey((v) => !v)}
            aria-label={showKey ? "Hide API key" : "Show API key"}
            className="absolute inset-y-0 right-0 flex w-9 items-center justify-center rounded-r-lg text-muted-foreground transition-colors outline-none hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50"
          >
            {showKey ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
          </button>
        </div>
      </Field>

      <Field
        label="Model"
        htmlFor="openrouter-model"
        helper="Any OpenRouter model id."
      >
        <Input
          id="openrouter-model"
          value={model}
          onChange={(e) => setModel(e.target.value)}
          placeholder="anthropic/claude-sonnet-4.5"
          autoComplete="off"
          spellCheck={false}
          aria-invalid={invalid}
        />
      </Field>
    </SettingsCard>
  )
}
