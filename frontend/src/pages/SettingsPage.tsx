import { useQuery } from "@tanstack/react-query"
import { RotateCw, ServerCrash } from "lucide-react"
import { api } from "@/lib/api"
import { EmptyState } from "@/components/empty-state"
import { PageHeader } from "@/components/page-header"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { BrowserSection } from "@/components/settings/browser-section"
import { ChatFlowSection } from "@/components/settings/chat-section"
import { IdentityCaptureSection } from "@/components/settings/identity-section"
import { LlmChatSection } from "@/components/settings/llm-section"
import { OpenRouterSection } from "@/components/settings/openrouter-section"
import { RefundSignalSection } from "@/components/settings/refund-signal-section"
import {
  SETTINGS_QUERY_KEY,
  type AppSettings,
} from "@/components/settings/shared"

function SettingsSkeleton() {
  return (
    <div className="max-w-3xl space-y-6">
      {[0, 1, 2].map((i) => (
        <Card key={i}>
          <CardHeader className="border-b">
            <div className="flex items-center gap-3.5">
              <Skeleton className="size-9 rounded-lg" />
              <div className="space-y-2">
                <Skeleton className="h-4 w-36" />
                <Skeleton className="h-3 w-72" />
              </div>
            </div>
          </CardHeader>
          <CardContent className="space-y-5">
            <div className="space-y-2">
              <Skeleton className="h-3.5 w-24" />
              <Skeleton className="h-8 w-full" />
            </div>
            <div className="space-y-2">
              <Skeleton className="h-3.5 w-32" />
              <Skeleton className="h-20 w-full" />
            </div>
          </CardContent>
        </Card>
      ))}
    </div>
  )
}

export default function SettingsPage() {
  const { data, isError, error, refetch } = useQuery({
    queryKey: SETTINGS_QUERY_KEY,
    queryFn: () => api.get<AppSettings>("/settings"),
  })

  return (
    <>
      <PageHeader
        title="Settings"
        description="Stored locally in SQLite — never committed or synced anywhere. Each section saves on its own."
      />

      {data ? (
        <div className="max-w-3xl space-y-6">
          <OpenRouterSection apiKey={data.openrouter_api_key} llm={data.llm} />
          <LlmChatSection llm={data.llm} />
          <ChatFlowSection chat={data.chat} />
          <RefundSignalSection refundSignal={data.refund_signal} />
          <IdentityCaptureSection identity={data.identity_capture} />
          <BrowserSection browser={data.browser} />
        </div>
      ) : isError ? (
        <EmptyState
          icon={ServerCrash}
          title="Couldn't load settings"
          description={
            error instanceof Error
              ? error.message
              : "The backend did not respond."
          }
          action={
            <Button onClick={() => refetch()}>
              <RotateCw />
              Retry
            </Button>
          }
        />
      ) : (
        <SettingsSkeleton />
      )}
    </>
  )
}
