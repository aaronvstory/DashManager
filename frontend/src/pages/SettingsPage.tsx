import { SlidersHorizontal } from "lucide-react"
import { EmptyState } from "@/components/empty-state"
import { PageHeader } from "@/components/page-header"

export default function SettingsPage() {
  return (
    <>
      <PageHeader
        title="Settings"
        description="Chat strategy, pacing, and app behavior."
      />
      <EmptyState
        icon={SlidersHorizontal}
        title="Nothing to configure yet"
        description="Strategy selection (scripted vs LLM), pacing controls, and data management arrive in a later wave."
      />
    </>
  )
}
