/**
 * Edit a CustomerDaisy record's identity/address fields from the web app.
 *
 * Backend: PATCH /api/daisy/{id} (DaisyPatch — whitelisted columns). We send
 * ONLY the fields the user actually changed (exclude-unset semantics), so an
 * untouched field is never overwritten and a no-op save sends an empty patch.
 * CustomerDaisy's DB is the source of truth for identity, so this writes there;
 * DashManager links by email and is unaffected.
 */

import { useState } from "react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { LoaderCircle } from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { api } from "@/lib/api"
import { apiErrorDetail } from "@/components/customers/helpers"

// The editable subset of a CustomerDaisy record (mirrors backend DaisyPatch).
// `phone` maps to primary_phone worker-side; `customer_id` is the route key.
export interface EditableDaisyCustomer {
  customer_id: string
  first_name?: string
  last_name?: string
  email?: string
  full_address?: string
  city?: string
  state?: string
  zip_code?: string
  phone?: string
}

// Form fields in display order. Keys match DaisyPatch exactly so the diff maps
// 1:1 to the PATCH body — no field-name translation layer to drift.
const FIELDS: { key: keyof EditableDaisyCustomer; label: string }[] = [
  { key: "first_name", label: "First name" },
  { key: "last_name", label: "Last name" },
  { key: "email", label: "Email" },
  { key: "phone", label: "Phone" },
  { key: "full_address", label: "Address" },
  { key: "city", label: "City" },
  { key: "state", label: "State" },
  { key: "zip_code", label: "ZIP" },
]

function toForm(c: EditableDaisyCustomer): Record<string, string> {
  const f: Record<string, string> = {}
  for (const { key } of FIELDS) f[key] = (c[key] as string | undefined) ?? ""
  return f
}

export function EditCustomerDialog({
  customer,
  open,
  onOpenChange,
}: {
  customer: EditableDaisyCustomer | null
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Edit CustomerDaisy record</DialogTitle>
          <DialogDescription>
            Updates the upstream pool. Identity lives in CustomerDaisy;
            DashManager links by email and is unaffected.
          </DialogDescription>
        </DialogHeader>
        {/* Key the form on the record + open so React remounts it with a fresh
            useState seed per customer — no effect-driven reseed (which the
            no-setState-in-effect rule rightly flags), and reopening the same
            row after a cancel discards the abandoned edits. */}
        {customer && (
          <EditForm
            key={`${customer.customer_id}:${String(open)}`}
            customer={customer}
            onOpenChange={onOpenChange}
          />
        )}
      </DialogContent>
    </Dialog>
  )
}

function EditForm({
  customer,
  onOpenChange,
}: {
  customer: EditableDaisyCustomer
  onOpenChange: (open: boolean) => void
}) {
  const qc = useQueryClient()
  const [form, setForm] = useState<Record<string, string>>(() => toForm(customer))

  const save = useMutation({
    mutationFn: (patch: Record<string, string>) =>
      api.patch(`/daisy/${encodeURIComponent(customer.customer_id)}`, patch),
    onSuccess: () => {
      toast.success("Saved to CustomerDaisy")
      // identity changed → the list row AND the adopted-by-email tag may move,
      // so refresh both the customer list and the coverage analytics.
      void qc.invalidateQueries({ queryKey: ["daisy"] })
      void qc.invalidateQueries({ queryKey: ["daisy-analytics"] })
      onOpenChange(false)
    },
    onError: (err) => toast.error(apiErrorDetail(err, "Save failed")),
  })

  function submit(e: React.FormEvent) {
    e.preventDefault()
    // Diff against the original: send only fields the user actually changed, so
    // we never re-write an untouched value (matches backend exclude-unset).
    const original = toForm(customer)
    const patch: Record<string, string> = {}
    for (const { key } of FIELDS) {
      const next = (form[key] ?? "").trim()
      if (next !== (original[key] ?? "")) patch[key] = next
    }
    if (Object.keys(patch).length === 0) {
      toast.info("No changes to save")
      onOpenChange(false)
      return
    }
    save.mutate(patch)
  }

  return (
    <form onSubmit={submit} className="grid grid-cols-2 gap-3">
      {FIELDS.map(({ key, label }) => (
        <div
          key={key}
          className={key === "full_address" ? "col-span-2" : undefined}
        >
          <Label htmlFor={`edit-${key}`} className="eyebrow">
            {label}
          </Label>
          <Input
            id={`edit-${key}`}
            value={form[key] ?? ""}
            onChange={(e) => setForm((f) => ({ ...f, [key]: e.target.value }))}
            className="mt-1 bg-card"
            autoComplete="off"
          />
        </div>
      ))}

      <DialogFooter className="col-span-2 mt-2">
        <Button
          type="button"
          variant="outline"
          onClick={() => onOpenChange(false)}
          disabled={save.isPending}
        >
          Cancel
        </Button>
        <Button type="submit" disabled={save.isPending}>
          {save.isPending && (
            <LoaderCircle data-icon="inline-start" className="animate-spin" />
          )}
          Save
        </Button>
      </DialogFooter>
    </form>
  )
}
