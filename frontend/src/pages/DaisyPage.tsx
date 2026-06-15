/**
 * CustomerDaisy — manage the upstream account pool from the web app.
 *
 * CustomerDaisy (the desktop tool the user creates accounts in) keeps its OWN
 * customer DB. This page is a window onto it: list / search / edit / delete /
 * export the pool, without opening CustomerDaisy's terminal UI.
 *
 * Sync model (read the backend route docstring for the full version): the
 * CustomerDaisy DB is the source of truth for identity + the api.cc number;
 * DashManager owns bucket/session/refund state and LINKS by email. Each row is
 * tagged `in_dashmanager` so you can see which are already adopted into a bucket
 * (adopt the rest via the Customers page's "Import from CustomerDaisy").
 */

import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Check, Database, Download, Pencil, RefreshCw, Trash2 } from "lucide-react"
import { toast } from "sonner"
import { EmptyState } from "@/components/empty-state"
import { PageHeader } from "@/components/page-header"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { api } from "@/lib/api"
import { cn } from "@/lib/utils"

interface DaisyCustomer {
  customer_id: string
  first_name: string
  last_name: string
  full_name: string
  email: string
  phone: string
  full_address: string
  city: string
  state: string
  created_at: string
  verification_completed: boolean
  in_dashmanager: boolean
}

interface DaisyListResponse {
  customers: DaisyCustomer[]
  count: number
}

export default function DaisyPage() {
  const qc = useQueryClient()
  const [q, setQ] = useState("")

  const list = useQuery({
    queryKey: ["daisy"],
    queryFn: () => api.get<DaisyListResponse>("/daisy"),
  })

  const del = useMutation({
    mutationFn: (id: string) => api.del(`/daisy/${id}`),
    onSuccess: () => {
      toast.success("Deleted from CustomerDaisy")
      void qc.invalidateQueries({ queryKey: ["daisy"] })
    },
    onError: () => toast.error("Delete failed"),
  })

  const all = list.data?.customers ?? []
  const needle = q.trim().toLowerCase()
  const rows = needle
    ? all.filter((c) =>
        [c.full_name, c.email, c.phone, c.full_address, c.city, c.state]
          .some((v) => (v || "").toLowerCase().includes(needle)),
      )
    : all

  return (
    <>
      <PageHeader
        title="CustomerDaisy"
        description="The upstream account pool — view, edit, delete, or export CustomerDaisy's own records. Rows already pulled into a DashManager bucket are tagged. Identity + number live here; DashManager links by email."
        actions={
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" render={<a href="/api/daisy/export/csv" />}>
              <Download data-icon="inline-start" />
              CSV
            </Button>
            <Button variant="outline" size="sm" render={<a href="/api/daisy/export/json" />}>
              <Download data-icon="inline-start" />
              JSON
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => void list.refetch()}
              disabled={list.isFetching}
            >
              <RefreshCw data-icon="inline-start" className={cn(list.isFetching && "animate-spin")} />
              Refresh
            </Button>
          </div>
        }
      />

      {list.isPending ? (
        <Skeleton className="h-96 w-full" />
      ) : list.isError ? (
        <div className="flex flex-col items-center gap-3 border border-border bg-card px-8 py-16 text-center">
          <p className="text-sm text-muted-foreground">
            Couldn't reach CustomerDaisy. Is its path set in Settings, and is the
            bridge reachable?
          </p>
          <Button variant="outline" size="sm" onClick={() => void list.refetch()}>
            Try again
          </Button>
        </div>
      ) : all.length === 0 ? (
        <EmptyState
          icon={Database}
          title="No CustomerDaisy records"
          description="Create accounts in CustomerDaisy (or check its path in Settings) and they'll show up here."
        />
      ) : (
        <div className="space-y-4">
          <div className="flex items-center justify-between gap-4">
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Search name, email, phone, address…"
              className="bx h-9 w-80 max-w-full bg-card px-3 text-sm outline-none focus:border-primary"
            />
            <span className="eyebrow">
              {rows.length} / {list.data?.count ?? all.length}
            </span>
          </div>

          <div className="border border-border bg-card">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border">
                  <th className="eyebrow px-4 py-2 text-left">Name</th>
                  <th className="eyebrow px-4 py-2 text-left">Email</th>
                  <th className="eyebrow px-4 py-2 text-left">Phone</th>
                  <th className="eyebrow px-4 py-2 text-left">Location</th>
                  <th className="eyebrow px-4 py-2 text-left">In DM</th>
                  <th className="px-4 py-2" />
                </tr>
              </thead>
              <tbody>
                {rows.map((c) => (
                  <tr key={c.customer_id} className="border-b border-border last:border-0">
                    <td className="px-4 py-2.5 font-medium">
                      {c.full_name || `${c.first_name} ${c.last_name}`.trim() || "—"}
                    </td>
                    <td className="num px-4 py-2.5 text-muted-foreground">{c.email || "—"}</td>
                    <td className="num px-4 py-2.5 text-muted-foreground">{c.phone || "—"}</td>
                    <td className="px-4 py-2.5 text-muted-foreground">
                      {[c.city, c.state].filter(Boolean).join(", ") || "—"}
                    </td>
                    <td className="px-4 py-2.5">
                      {c.in_dashmanager ? (
                        <span className="inline-flex items-center gap-1 text-xs font-semibold text-emerald-500">
                          <Check className="size-3.5" /> adopted
                        </span>
                      ) : (
                        <span className="text-xs text-muted-foreground">—</span>
                      )}
                    </td>
                    <td className="px-4 py-2.5 text-right">
                      <div className="flex items-center justify-end gap-1.5">
                        <Button
                          variant="ghost"
                          size="icon"
                          title="Edit (coming soon)"
                          disabled
                        >
                          <Pencil className="size-4" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          title="Delete from CustomerDaisy"
                          onClick={() => {
                            if (
                              window.confirm(
                                `Delete ${c.full_name || c.email} from CustomerDaisy? This does not affect DashManager.`,
                              )
                            ) {
                              del.mutate(c.customer_id)
                            }
                          }}
                          disabled={del.isPending}
                        >
                          <Trash2 className="size-4 text-primary" />
                        </Button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </>
  )
}
