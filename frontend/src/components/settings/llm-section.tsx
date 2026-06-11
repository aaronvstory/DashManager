import { useState } from "react"
import { Sparkles } from "lucide-react"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import {
  Field,
  parsePositiveInt,
  SettingsCard,
  useSaveSettings,
  type LlmSettings,
} from "@/components/settings/shared"

/** LLM chat behavior — system prompt + turn budget (inside the 'llm' object). */
export function LlmChatSection({ llm }: { llm: LlmSettings }) {
  const [systemPrompt, setSystemPrompt] = useState(llm.system_prompt)
  const [maxTurns, setMaxTurns] = useState(String(llm.max_turns))
  const [baseline, setBaseline] = useState({
    systemPrompt: llm.system_prompt,
    maxTurns: String(llm.max_turns),
  })

  const parsedTurns = parsePositiveInt(maxTurns)
  const dirty =
    systemPrompt !== baseline.systemPrompt || maxTurns !== baseline.maxTurns
  const invalid = parsedTurns === null
  const save = useSaveSettings("LLM chat")

  function handleSave() {
    if (parsedTurns === null) return
    save.mutate(
      [
        {
          key: "llm",
          value: { ...llm, system_prompt: systemPrompt, max_turns: parsedTurns },
        },
      ],
      { onSuccess: () => setBaseline({ systemPrompt, maxTurns }) },
    )
  }

  return (
    <SettingsCard
      icon={Sparkles}
      title="LLM chat"
      description="How the model behaves when the run uses the LLM chat strategy."
      dirty={dirty}
      saving={save.isPending}
      invalid={invalid}
      onSave={handleSave}
    >
      <Field
        label="System prompt"
        htmlFor="llm-system-prompt"
        helper="Empty = built-in prompt that insists on refunds to the original card and refuses credits."
      >
        <Textarea
          id="llm-system-prompt"
          value={systemPrompt}
          onChange={(e) => setSystemPrompt(e.target.value)}
          placeholder="Leave empty to use the built-in prompt…"
          className="min-h-28"
        />
      </Field>

      <Field
        label="Max turns"
        htmlFor="llm-max-turns"
        helper="Hard cap on model replies per chat before the chat is abandoned."
        className="max-w-48"
      >
        <Input
          id="llm-max-turns"
          type="number"
          min={1}
          value={maxTurns}
          onChange={(e) => setMaxTurns(e.target.value)}
          aria-invalid={parsedTurns === null}
        />
      </Field>
    </SettingsCard>
  )
}
