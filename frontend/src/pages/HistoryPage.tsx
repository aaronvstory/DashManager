import { History } from "lucide-react"
import { EmptyState } from "@/components/empty-state"
import { PageHeader } from "@/components/page-header"

export default function HistoryPage() {
  return (
    <>
      <PageHeader
        title="History"
        description="Past runs with their stats, chat transcripts, and refund outcomes."
      />
      <EmptyState
        icon={History}
        title="No runs recorded"
        description="Once a run completes it shows up here with per-customer results and full chat transcripts."
      />
    </>
  )
}
