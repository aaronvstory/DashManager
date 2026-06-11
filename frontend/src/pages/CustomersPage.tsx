import { Plus, Users } from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { EmptyState } from "@/components/empty-state"
import { PageHeader } from "@/components/page-header"

export default function CustomersPage() {
  return (
    <>
      <PageHeader
        title="Customers"
        description="Manage DoorDash customer accounts, their captured sessions, and bucket dates."
        actions={
          <Button onClick={() => toast.info("Customer onboarding lands in the next wave.")}>
            <Plus data-icon="inline-start" />
            Add customer
          </Button>
        }
      />
      <EmptyState
        icon={Users}
        title="No customers yet"
        description="Add a customer to capture their DoorDash session and start tracking orders for refund checks."
      />
    </>
  )
}
