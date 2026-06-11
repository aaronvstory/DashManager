/**
 * Chat transcript rendered as message bubbles:
 *   out    -> right-aligned, primary tint (us / the bot)
 *   in     -> left-aligned, muted (DoorDash support)
 *   system -> centered, subtle italic (automation notes)
 *
 * Long transcripts get a fixed-height ScrollArea; short ones render inline
 * so a two-line chat doesn't sit inside an empty scroll box.
 */

import { format } from "date-fns"
import { ScrollArea } from "@/components/ui/scroll-area"
import { cn } from "@/lib/utils"
import type { ChatMessage } from "@/lib/types"
import { parseDbDate } from "./run-data"

function Bubble({ message }: { message: ChatMessage }) {
  if (message.direction === "system") {
    return (
      <div className="max-w-[85%] self-center text-center text-xs text-muted-foreground/80 italic">
        {message.content}
      </div>
    )
  }

  const out = message.direction === "out"
  return (
    <div
      className={cn(
        "flex max-w-[80%] flex-col gap-1",
        out ? "items-end self-end" : "items-start self-start",
      )}
    >
      <div
        className={cn(
          "rounded-2xl px-3.5 py-2 text-sm leading-relaxed break-words whitespace-pre-wrap",
          out
            ? "rounded-br-sm border border-primary/25 bg-primary/10"
            : "rounded-bl-sm bg-muted",
        )}
      >
        {message.content}
      </div>
      <span className="px-1 text-[10px] text-muted-foreground/70 tabular-nums">
        {out ? "Sent" : "Support"} · {format(parseDbDate(message.ts), "h:mm:ss a")}
      </span>
    </div>
  )
}

export function ChatTranscript({ messages }: { messages: ChatMessage[] }) {
  if (messages.length === 0) {
    return (
      <p className="px-4 py-6 text-center text-xs text-muted-foreground italic">
        No messages were recorded for this chat.
      </p>
    )
  }

  const body = (
    <div className="flex flex-col gap-2.5 p-4">
      {messages.map((message) => (
        <Bubble key={message.id} message={message} />
      ))}
    </div>
  )

  // Explicit height (not max-h) so the Base UI viewport actually scrolls.
  if (messages.length > 6) {
    return <ScrollArea className="h-72">{body}</ScrollArea>
  }
  return body
}
