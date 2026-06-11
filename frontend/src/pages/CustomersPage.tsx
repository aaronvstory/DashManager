import { useEffect, useMemo, useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { format } from "date-fns"
import { Plus, Sparkles, UserPlus } from "lucide-react"
import { useNavigate } from "react-router-dom"
import { toast } from "sonner"
import { AddCustomerDialog } from "@/components/customers/add-customer-dialog"
import { CreateAccountDialog } from "@/components/customers/create-account-dialog"
import { BucketCard } from "@/components/customers/bucket-card"
import { DeleteCustomerDialog } from "@/components/customers/delete-customer-dialog"
import { EditCustomerDialog } from "@/components/customers/edit-customer-dialog"
import {
  apiErrorDetail,
  customerName,
  parseBucketDate,
} from "@/components/customers/helpers"
import { SelectionBar } from "@/components/customers/selection-bar"
import { PageHeader } from "@/components/page-header"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { api, ApiError } from "@/lib/api"
import type { Customer, StrategyName } from "@/lib/types"

type RunScope = { bucket_date: string } | { customer_ids: number[] }

function LoadingSkeleton() {
  return (
    <div className="space-y-6">
      {[0, 1].map((i) => (
        <Card key={i}>
          <CardHeader>
            <Skeleton className="h-5 w-44" />
            <Skeleton className="h-4 w-24" />
          </CardHeader>
          <CardContent className="space-y-3">
            {[0, 1, 2].map((j) => (
              <Skeleton key={j} className="h-9 w-full" />
            ))}
          </CardContent>
        </Card>
      ))}
    </div>
  )
}

function EmptyHero({ onAdd }: { onAdd: () => void }) {
  return (
    <Card className="relative flex flex-col items-center gap-2 overflow-hidden px-8 py-24 text-center">
      <div
        aria-hidden
        className="pointer-events-none absolute inset-x-0 -top-32 h-64 bg-primary/10 blur-3xl"
      />
      <div className="mb-4 flex size-14 items-center justify-center rounded-2xl bg-primary/10 ring-1 ring-primary/20">
        <UserPlus className="size-6 text-primary" />
      </div>
      <h2 className="font-heading text-xl font-semibold">Add your first customer</h2>
      <p className="max-w-md text-sm text-balance text-muted-foreground">
        DashManager opens a Chromium window where the customer logs into DoorDash. The
        session and profile are captured automatically — no manual credential handling.
      </p>
      <Button size="lg" className="mt-5" onClick={onAdd}>
        <Plus data-icon="inline-start" />
        Add customer
      </Button>
    </Card>
  )
}

export default function CustomersPage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const { data, isLoading } = useQuery({
    queryKey: ["customers"],
    queryFn: () => api.get<{ customers: Customer[] }>("/customers"),
  })
  const customers = useMemo(() => data?.customers ?? [], [data])

  const [addOpen, setAddOpen] = useState(false)
  const [createOpen, setCreateOpen] = useState(false)
  const [editing, setEditing] = useState<Customer | null>(null)
  const [deleting, setDeleting] = useState<Customer | null>(null)
  const [selectedIds, setSelectedIds] = useState<ReadonlySet<number>>(new Set())
  const [testingIds, setTestingIds] = useState<ReadonlySet<number>>(new Set())
  const [strategy, setStrategy] = useState<StrategyName>("scripted")
  const [startingRun, setStartingRun] = useState(false)

  /** bucket_date → customers, newest bucket first. */
  const buckets = useMemo(() => {
    const map = new Map<string, Customer[]>()
    for (const c of customers) {
      const list = map.get(c.bucket_date)
      if (list) list.push(c)
      else map.set(c.bucket_date, [c])
    }
    return [...map.entries()].sort((a, b) => (a[0] < b[0] ? 1 : -1))
  }, [customers])

  // Drop selections for customers that no longer exist (e.g. after delete).
  useEffect(() => {
    setSelectedIds((prev) => {
      const valid = new Set(customers.map((c) => c.id))
      const next = new Set([...prev].filter((id) => valid.has(id)))
      return next.size === prev.size ? prev : next
    })
  }, [customers])

  function toggleRow(id: number, checked: boolean) {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (checked) next.add(id)
      else next.delete(id)
      return next
    })
  }

  function toggleBucket(ids: number[], checked: boolean) {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      for (const id of ids) {
        if (checked) next.add(id)
        else next.delete(id)
      }
      return next
    })
  }

  async function startRun(scope: RunScope) {
    setStartingRun(true)
    try {
      await api.post<{ run_id: number }>("/runs", { scope, chat_strategy: strategy })
      navigate("/run")
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        toast.error("A run is already active")
        navigate("/run")
      } else {
        toast.error(apiErrorDetail(err, "Could not start the run"))
      }
    } finally {
      setStartingRun(false)
    }
  }

  async function testSession(c: Customer) {
    setTestingIds((prev) => new Set(prev).add(c.id))
    try {
      const res = await api.post<{ ok: boolean; orders_count?: number; error?: string }>(
        `/customers/${c.id}/test-session`,
      )
      if (res.ok) {
        toast.success(`Session OK — ${res.orders_count ?? 0} orders`)
      } else {
        toast.warning("Session expired — log in again")
      }
      void queryClient.invalidateQueries({ queryKey: ["customers"] })
    } catch (err) {
      toast.error(apiErrorDetail(err, "Session test failed"))
    } finally {
      setTestingIds((prev) => {
        const next = new Set(prev)
        next.delete(c.id)
        return next
      })
    }
  }

  async function moveCustomer(c: Customer, newDate: string) {
    if (newDate === c.bucket_date) return
    try {
      await api.patch<Customer>(`/customers/${c.id}`, { bucket_date: newDate })
      toast.success(
        `Moved ${customerName(c)} to ${format(parseBucketDate(newDate), "MMM d, yyyy")}`,
      )
      void queryClient.invalidateQueries({ queryKey: ["customers"] })
    } catch (err) {
      toast.error(apiErrorDetail(err, "Could not move customer"))
    }
  }

  const selectionActive = selectedIds.size > 0

  return (
    <>
      <PageHeader
        title="Customers"
        description={
          customers.length > 0
            ? `${customers.length} customer${customers.length === 1 ? "" : "s"} across ${buckets.length} bucket${buckets.length === 1 ? "" : "s"}.`
            : "Manage DoorDash customer accounts, their captured sessions, and bucket dates."
        }
        actions={
          <div className="flex items-center gap-2">
            <Button variant="outline" onClick={() => setCreateOpen(true)}>
              <Sparkles data-icon="inline-start" />
              Create account
            </Button>
            <Button onClick={() => setAddOpen(true)}>
              <Plus data-icon="inline-start" />
              Add customer
            </Button>
          </div>
        }
      />

      {isLoading ? (
        <LoadingSkeleton />
      ) : customers.length === 0 ? (
        <EmptyHero onAdd={() => setAddOpen(true)} />
      ) : (
        <div className="space-y-6">
          {buckets.map(([date, bucketCustomers]) => (
            <BucketCard
              key={date}
              date={date}
              customers={bucketCustomers}
              selectedIds={selectedIds}
              testingIds={testingIds}
              runDisabled={startingRun}
              onToggleRow={toggleRow}
              onToggleBucket={toggleBucket}
              onRunBucket={(bucketDate) => void startRun({ bucket_date: bucketDate })}
              onEdit={setEditing}
              onDelete={setDeleting}
              onTestSession={(c) => void testSession(c)}
              onMove={(c, newDate) => void moveCustomer(c, newDate)}
            />
          ))}
          {/* Keep the last bucket clear of the floating selection bar. */}
          {selectionActive ? <div aria-hidden className="h-20" /> : null}
        </div>
      )}

      {selectionActive ? (
        <SelectionBar
          count={selectedIds.size}
          strategy={strategy}
          starting={startingRun}
          onStrategyChange={setStrategy}
          onRun={() => void startRun({ customer_ids: [...selectedIds] })}
          onClear={() => setSelectedIds(new Set())}
        />
      ) : null}

      <AddCustomerDialog open={addOpen} onOpenChange={setAddOpen} />

      <CreateAccountDialog open={createOpen} onOpenChange={setCreateOpen} />

      {editing ? (
        <EditCustomerDialog
          key={editing.id}
          customer={editing}
          onClose={() => setEditing(null)}
        />
      ) : null}

      {deleting ? (
        <DeleteCustomerDialog
          key={deleting.id}
          customer={deleting}
          onClose={() => setDeleting(null)}
        />
      ) : null}
    </>
  )
}
