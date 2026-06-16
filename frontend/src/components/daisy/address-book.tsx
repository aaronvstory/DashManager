/**
 * Anchor Address Book — read/add/edit/delete CustomerDaisy's my_addresses.json
 * interactively. The anchor pool is the user's own saved addresses, offered in
 * the create-account dialog as a batch origin alongside the predefined
 * locations. Backed by /api/daisy/addresses (GET list, POST add, PATCH /{index},
 * DELETE /{index}) + /api/daisy/generate-address (a real MapQuest address near
 * an origin); the worker persists my_addresses.json atomically.
 */
import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Check, MapPin, Pencil, Plus, Sparkles, Trash2, X } from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { api } from "@/lib/api"

interface Anchor {
  name: string
  full_address: string
  city: string
  state: string
}

interface AnchorResponse {
  addresses: Anchor[]
}

interface GenerateResponse {
  address: { full_address?: string; city?: string; state?: string } | null
}

const EMPTY: Anchor = { name: "", full_address: "", city: "", state: "" }

export function AddressBook() {
  const qc = useQueryClient()
  const [draft, setDraft] = useState<Anchor>(EMPTY)
  const [editIndex, setEditIndex] = useState<number | null>(null)
  const [editDraft, setEditDraft] = useState<Anchor>(EMPTY)
  const [origin, setOrigin] = useState("")

  const q = useQuery({
    queryKey: ["daisy-address-book"],
    queryFn: () => api.get<AnchorResponse>("/daisy/addresses"),
  })

  const invalidate = () => {
    // the create-account dialog reads the same pool — refresh both.
    void qc.invalidateQueries({ queryKey: ["daisy-address-book"] })
    void qc.invalidateQueries({ queryKey: ["daisy-addresses"] })
  }

  const add = useMutation({
    mutationFn: (a: Anchor) => api.post<AnchorResponse>("/daisy/addresses", a),
    onSuccess: () => {
      toast.success("Address added")
      setDraft(EMPTY)
      invalidate()
    },
    onError: () => toast.error("Couldn't add address"),
  })

  const update = useMutation({
    mutationFn: ({ index, a }: { index: number; a: Anchor }) =>
      api.patch<AnchorResponse>(`/daisy/addresses/${index}`, a),
    onSuccess: () => {
      toast.success("Address updated")
      setEditIndex(null)
      invalidate()
    },
    onError: () => toast.error("Couldn't update address"),
  })

  const del = useMutation({
    mutationFn: (index: number) => api.del(`/daisy/addresses/${index}`),
    onSuccess: () => {
      toast.success("Address removed")
      invalidate()
    },
    onError: () => toast.error("Couldn't remove address"),
  })

  // Generate a real MapQuest address near the typed origin and drop it into the
  // add-form draft (ready to review + Add) — does NOT save it directly.
  const generate = useMutation({
    mutationFn: (originAddress: string) =>
      api.post<GenerateResponse>("/daisy/generate-address", {
        origin_address: originAddress,
      }),
    onSuccess: (res) => {
      const a = res.address
      if (!a?.full_address) {
        toast.warning("No address found near that origin — try another")
        return
      }
      setDraft({
        name: "",
        full_address: a.full_address,
        city: a.city ?? "",
        state: a.state ?? "",
      })
      toast.success("Generated — review and Add")
    },
    onError: () => toast.error("Couldn't generate an address"),
  })

  const rows = q.data?.addresses ?? []
  const canAdd = draft.full_address.trim().length > 0
  // Index-based identity: while ANY mutation is in flight the list may be about
  // to shift, so disable every mutating control to prevent acting on a stale
  // index (the refetch on success re-renders with fresh indices).
  const busy = add.isPending || update.isPending || del.isPending
  const canGenerate = origin.trim().length > 0

  return (
    <section className="mt-10 border border-border bg-card">
      <header className="flex items-center gap-2 border-b border-border px-4 py-3">
        <MapPin className="size-4 text-muted-foreground" />
        <h2 className="font-mono text-sm font-semibold uppercase tracking-wide">
          Anchor Address Book
        </h2>
        <span className="ml-auto font-mono text-xs text-muted-foreground">
          {rows.length} saved
        </span>
      </header>

      <p className="px-4 pt-3 text-xs text-muted-foreground">
        Your own saved addresses (CustomerDaisy's <code>my_addresses.json</code>).
        Offered as a batch origin in the create-account dialog. Full address is
        required; name/city/state are optional labels.
      </p>

      {/* Generate-near-origin: fills the add form with a real MapQuest address
          near the given origin (review, then Add). */}
      <div className="flex flex-col gap-2 px-4 pt-3 sm:flex-row sm:items-center">
        <Input
          placeholder="Generate near an origin address (e.g. 706 N Broad St, Edenton NC)…"
          value={origin}
          disabled={busy || generate.isPending}
          onChange={(e) => setOrigin(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && canGenerate && !busy && !generate.isPending)
              generate.mutate(origin.trim())
          }}
        />
        <Button
          variant="outline"
          size="sm"
          className="shrink-0"
          disabled={!canGenerate || busy || generate.isPending}
          onClick={() => generate.mutate(origin.trim())}
        >
          <Sparkles data-icon="inline-start" />
          {generate.isPending ? "Generating…" : "Generate"}
        </Button>
      </div>

      {/* Add row */}
      <div className="grid grid-cols-1 gap-2 px-4 py-3 sm:grid-cols-[1fr_2fr_1fr_auto_auto]">
        <Input
          placeholder="Name (optional)"
          value={draft.name}
          onChange={(e) => setDraft({ ...draft, name: e.target.value })}
        />
        <Input
          placeholder="Full address *"
          value={draft.full_address}
          onChange={(e) => setDraft({ ...draft, full_address: e.target.value })}
          onKeyDown={(e) => {
            if (e.key === "Enter" && canAdd && !busy && !generate.isPending)
              add.mutate(draft)
          }}
        />
        <Input
          placeholder="City"
          value={draft.city}
          onChange={(e) => setDraft({ ...draft, city: e.target.value })}
        />
        <Input
          placeholder="ST"
          className="sm:w-16"
          value={draft.state}
          onChange={(e) => setDraft({ ...draft, state: e.target.value })}
        />
        <Button
          size="sm"
          disabled={!canAdd || busy || generate.isPending}
          onClick={() => add.mutate(draft)}
        >
          <Plus data-icon="inline-start" />
          Add
        </Button>
      </div>

      {q.isPending ? (
        <Skeleton className="mx-4 mb-4 h-24" />
      ) : rows.length === 0 ? (
        <p className="px-4 pb-4 text-sm text-muted-foreground">
          No saved addresses yet — add one above.
        </p>
      ) : (
        <ul className="divide-y divide-border border-t border-border">
          {rows.map((a, i) =>
            editIndex === i ? (
              <li key={i} className="grid grid-cols-1 gap-2 px-4 py-3 sm:grid-cols-[1fr_2fr_1fr_auto_auto]">
                <Input
                  value={editDraft.name}
                  onChange={(e) => setEditDraft({ ...editDraft, name: e.target.value })}
                />
                <Input
                  value={editDraft.full_address}
                  onChange={(e) =>
                    setEditDraft({ ...editDraft, full_address: e.target.value })
                  }
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && editDraft.full_address.trim() && !busy)
                      update.mutate({ index: i, a: editDraft })
                    if (e.key === "Escape") setEditIndex(null)
                  }}
                />
                <Input
                  value={editDraft.city}
                  onChange={(e) => setEditDraft({ ...editDraft, city: e.target.value })}
                />
                <Input
                  className="sm:w-16"
                  value={editDraft.state}
                  onChange={(e) => setEditDraft({ ...editDraft, state: e.target.value })}
                />
                <div className="flex gap-1">
                  <Button
                    size="icon-sm"
                    disabled={!editDraft.full_address.trim() || busy}
                    onClick={() => update.mutate({ index: i, a: editDraft })}
                    aria-label="Save"
                  >
                    <Check />
                  </Button>
                  <Button
                    size="icon-sm"
                    variant="outline"
                    onClick={() => setEditIndex(null)}
                    aria-label="Cancel"
                  >
                    <X />
                  </Button>
                </div>
              </li>
            ) : (
              <li key={i} className="flex items-center gap-3 px-4 py-3">
                <div className="min-w-0 flex-1">
                  <p className="truncate font-mono text-sm">{a.full_address}</p>
                  {(a.name || a.city || a.state) && (
                    <p className="truncate text-xs text-muted-foreground">
                      {[a.name, a.city, a.state].filter(Boolean).join(" · ")}
                    </p>
                  )}
                </div>
                <Button
                  size="icon-sm"
                  variant="ghost"
                  aria-label="Edit"
                  disabled={busy}
                  onClick={() => {
                    setEditIndex(i)
                    setEditDraft(a)
                  }}
                >
                  <Pencil />
                </Button>
                <Button
                  size="icon-sm"
                  variant="ghost"
                  aria-label="Remove"
                  disabled={busy}
                  onClick={() => del.mutate(i)}
                >
                  <Trash2 />
                </Button>
              </li>
            ),
          )}
        </ul>
      )}
    </section>
  )
}
