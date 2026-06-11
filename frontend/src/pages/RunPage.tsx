import { Play, Radar } from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { EmptyState } from "@/components/empty-state"
import { PageHeader } from "@/components/page-header"

export default function RunPage() {
  return (
    <>
      <PageHeader
        title="Run"
        description="Launch refund-check runs and watch orders, chats, and outcomes stream in live."
        actions={
          <Button onClick={() => toast.info("Run controls land in the next wave.")}>
            <Play data-icon="inline-start" />
            Start run
          </Button>
        }
      />
      <EmptyState
        icon={Radar}
        title="No active run"
        description="Start a run to scrape orders, detect missing refunds, and open support chats automatically."
      />
    </>
  )
}
