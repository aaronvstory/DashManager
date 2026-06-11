import { useState } from "react"
import { MessagesSquare } from "lucide-react"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import {
  Field,
  linesToList,
  listToLines,
  parsePositiveInt,
  SettingsCard,
  Token,
  useSaveSettings,
  type ChatSettings,
} from "@/components/settings/shared"

/**
 * Scripted chat flow + escalation knobs — the full 'chat' settings object.
 * (chat.max_turns is preserved untouched via spread.)
 */
export function ChatFlowSection({ chat }: { chat: ChatSettings }) {
  const initial = {
    opening: chat.opening_template,
    agentWord: chat.agent_word,
    botPatterns: listToLines(chat.bot_patterns),
    successPhrases: listToLines(chat.success_phrases),
    followups: listToLines(chat.scripted_followups),
    maxEscalations: String(chat.max_escalations),
    maxChatSeconds: String(chat.max_chat_seconds),
  }
  const [form, setForm] = useState(initial)
  const [baseline, setBaseline] = useState(initial)

  const set = <K extends keyof typeof initial>(field: K, value: string) =>
    setForm((f) => ({ ...f, [field]: value }))

  const parsedEscalations = parsePositiveInt(form.maxEscalations)
  const parsedSeconds = parsePositiveInt(form.maxChatSeconds)
  const dirty = JSON.stringify(form) !== JSON.stringify(baseline)
  const invalid = parsedEscalations === null || parsedSeconds === null
  const save = useSaveSettings("Chat flow")

  function handleSave() {
    if (parsedEscalations === null || parsedSeconds === null) return
    save.mutate(
      [
        {
          key: "chat",
          value: {
            ...chat,
            opening_template: form.opening,
            agent_word: form.agentWord.trim(),
            bot_patterns: linesToList(form.botPatterns),
            success_phrases: linesToList(form.successPhrases),
            scripted_followups: linesToList(form.followups),
            max_escalations: parsedEscalations,
            max_chat_seconds: parsedSeconds,
          },
        },
      ],
      { onSuccess: () => setBaseline(form) },
    )
  }

  return (
    <SettingsCard
      icon={MessagesSquare}
      title="Chat flow"
      description="The message script, bot detection, and escalation limits used in every support chat."
      dirty={dirty}
      saving={save.isPending}
      invalid={invalid}
      onSave={handleSave}
    >
      <Field
        label="Opening message template"
        htmlFor="chat-opening"
        helper={
          <>
            Sent to open every chat. <Token>{"{order_count}"}</Token> becomes the
            number of orders, <Token>{"{amounts}"}</Token> their dollar amounts.
          </>
        }
      >
        <Textarea
          id="chat-opening"
          value={form.opening}
          onChange={(e) => set("opening", e.target.value)}
          className="min-h-24"
        />
      </Field>

      <div className="grid gap-5 sm:grid-cols-3">
        <Field
          label="Agent word"
          htmlFor="chat-agent-word"
          helper="Typed repeatedly to escalate past the bot to a human."
        >
          <Input
            id="chat-agent-word"
            value={form.agentWord}
            onChange={(e) => set("agentWord", e.target.value)}
            autoComplete="off"
            spellCheck={false}
          />
        </Field>
        <Field
          label="Max escalations"
          htmlFor="chat-max-escalations"
          helper="Agent-word attempts before giving up on the chat."
        >
          <Input
            id="chat-max-escalations"
            type="number"
            min={1}
            value={form.maxEscalations}
            onChange={(e) => set("maxEscalations", e.target.value)}
            aria-invalid={parsedEscalations === null}
          />
        </Field>
        <Field
          label="Max chat seconds"
          htmlFor="chat-max-seconds"
          helper="Hard time limit per chat, in seconds."
        >
          <Input
            id="chat-max-seconds"
            type="number"
            min={1}
            value={form.maxChatSeconds}
            onChange={(e) => set("maxChatSeconds", e.target.value)}
            aria-invalid={parsedSeconds === null}
          />
        </Field>
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        <Field
          label="Bot patterns"
          htmlFor="chat-bot-patterns"
          helper="One per line. Replies containing any of these are treated as bot responses and escalated."
        >
          <Textarea
            id="chat-bot-patterns"
            value={form.botPatterns}
            onChange={(e) => set("botPatterns", e.target.value)}
            className="min-h-24 font-mono text-xs"
            spellCheck={false}
          />
        </Field>
        <Field
          label="Success phrases"
          htmlFor="chat-success-phrases"
          helper="One per line. An agent reply containing any of these counts as a confirmed refund."
        >
          <Textarea
            id="chat-success-phrases"
            value={form.successPhrases}
            onChange={(e) => set("successPhrases", e.target.value)}
            className="min-h-24 font-mono text-xs"
            spellCheck={false}
          />
        </Field>
      </div>

      <Field
        label="Scripted follow-ups"
        htmlFor="chat-followups"
        helper="One per line. Sent in order once a human agent joins (scripted strategy only)."
      >
        <Textarea
          id="chat-followups"
          value={form.followups}
          onChange={(e) => set("followups", e.target.value)}
          className="min-h-20 font-mono text-xs"
          spellCheck={false}
        />
      </Field>
    </SettingsCard>
  )
}
