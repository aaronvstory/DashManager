import { useState } from "react"
import { ChevronRight, MessagesSquare } from "lucide-react"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { ScrollArea } from "@/components/ui/scroll-area"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"
import { ChatTranscript } from "@/components/chat-transcript"
import { InChatBadge, OutcomeBadge } from "@/components/run/badges"
import { useRunStore } from "@/store/runStore"
import type { LiveChat } from "@/store/runStore"

function customerLabel(
  chat: LiveChat,
  names: Map<number, string>,
): string {
  return names.get(chat.customer_id) ?? `Customer #${chat.customer_id}`
}

/**
 * One row per support chat. Clicking a row opens a Sheet with the live
 * transcript (it keeps updating while open — messages stream from the store).
 */
export function ChatsPanel() {
  const chats = useRunStore((s) => s.chats)
  const customers = useRunStore((s) => s.customersProgress)
  const [openChatId, setOpenChatId] = useState<number | null>(null)

  const names = new Map(customers.map((c) => [c.id, c.name]))
  const list = Object.values(chats).sort((a, b) => b.chat_id - a.chat_id)
  const openChat = openChatId !== null ? (chats[openChatId] ?? null) : null

  return (
    <>
      <Card className="gap-3">
        <CardHeader>
          <CardTitle>Support chats</CardTitle>
          <CardDescription>
            {list.length === 0
              ? "Opened when an order is missing its refund."
              : `${list.length} chat${list.length === 1 ? "" : "s"} this run`}
          </CardDescription>
        </CardHeader>
        <CardContent className="px-0">
          {list.length === 0 ? (
            <div className="flex flex-col items-center gap-2 px-4 py-10 text-center">
              <MessagesSquare className="size-6 text-muted-foreground/60" />
              <p className="text-sm text-muted-foreground">No chats opened yet.</p>
            </div>
          ) : (
            <ScrollArea className="max-h-80">
              <ul className="divide-y divide-border">
                {list.map((chat) => (
                  <li key={chat.chat_id}>
                    <button
                      type="button"
                      onClick={() => setOpenChatId(chat.chat_id)}
                      className="flex w-full items-center gap-3 px-4 py-2.5 text-left transition-colors hover:bg-muted/50"
                    >
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-sm font-medium">
                          {customerLabel(chat, names)}
                        </div>
                        <div className="text-xs text-muted-foreground">
                          {chat.attempt_no && chat.attempt_no > 1
                            ? `Attempt ${chat.attempt_no} · `
                            : ""}
                          {chat.messages.length} message
                          {chat.messages.length === 1 ? "" : "s"}
                          {chat.escalations > 0
                            ? ` · ${chat.escalations} escalation${chat.escalations === 1 ? "" : "s"}`
                            : ""}
                        </div>
                      </div>
                      {chat.outcome ? (
                        <OutcomeBadge outcome={chat.outcome} />
                      ) : (
                        <InChatBadge />
                      )}
                      <ChevronRight className="size-4 shrink-0 text-muted-foreground/60" />
                    </button>
                  </li>
                ))}
              </ul>
            </ScrollArea>
          )}
        </CardContent>
      </Card>

      <Sheet
        open={openChat !== null}
        onOpenChange={(open) => {
          if (!open) setOpenChatId(null)
        }}
      >
        <SheetContent side="right" className="sm:max-w-md">
          {openChat ? (
            <>
              <SheetHeader className="border-b">
                <SheetTitle className="flex items-center gap-2 pr-8">
                  {customerLabel(openChat, names)}
                  {openChat.outcome ? (
                    <OutcomeBadge outcome={openChat.outcome} />
                  ) : (
                    <InChatBadge />
                  )}
                </SheetTitle>
                <SheetDescription>
                  Chat #{openChat.chat_id} · {openChat.order_ids.length} order
                  {openChat.order_ids.length === 1 ? "" : "s"}
                  {openChat.agent_reached ? " · agent reached" : ""}
                </SheetDescription>
              </SheetHeader>
              <ScrollArea className="min-h-0 flex-1">
                <ChatTranscript messages={openChat.messages} className="p-4" />
              </ScrollArea>
            </>
          ) : null}
        </SheetContent>
      </Sheet>
    </>
  )
}
