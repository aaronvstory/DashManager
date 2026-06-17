import { useEffect, useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { format } from "date-fns"
import {
  CalendarDays,
  CircleAlert,
  Download,
  LoaderCircle,
  MapPin,
} from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Calendar } from "@/components/ui/calendar"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Label } from "@/components/ui/label"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { api } from "@/lib/api"
import { cn } from "@/lib/utils"
import { apiErrorDetail, parseDbTimestamp } from "./helpers"

interface DaisyCustomer {
  customer_id: string
  first_name: string
  last_name: string
  email: string
  phone: string
  full_address: string
  number_token: string
  created_at: string
}

interface RecentResponse {
  customers: DaisyCustomer[]
}

interface ImportResponse {
  imported: { id: number; name: string }[]
}

function daisyName(c: DaisyCustomer): string {
  return `${c.first_name} ${c.last_name}`.trim() || c.customer_id
}

export function ImportDaisyDialog({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const queryClient = useQueryClient()

  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set())
  const [date, setDate] = useState<Date>(() => new Date())
  const [dateOpen, setDateOpen] = useState(false)
  const [importing, setImporting] = useState(false)

  const recentQuery = useQuery({
    queryKey: ["daisy-recent"],
    queryFn: () =>
      api.get<RecentResponse>("/customers/daisy/recent?limit=20"),
    enabled: open,
    staleTime: 30_000,
  })
  const accounts = recentQuery.data?.customers ?? []

  // Drop selections that vanished after a refetch.
  useEffect(() => {
    setSelected((prev) => {
      const valid = new Set(accounts.map((c) => c.customer_id))
      const next = new Set([...prev].filter((id) => valid.has(id)))
      return next.size === prev.size ? prev : next
    })
  }, [accounts])

  function handleOpenChange(next: boolean) {
    onOpenChange(next)
    if (!next) {
      setSelected(new Set())
      setDate(new Date())
      setDateOpen(false)
    }
  }

  function toggle(id: string, checked: boolean) {
    setSelected((prev) => {
      const next = new Set(prev)
      if (checked) next.add(id)
      else next.delete(id)
      return next
    })
  }

  async function doImport() {
    if (selected.size === 0) return
    setImporting(true)
    try {
      const res = await api.post<ImportResponse>("/customers/daisy/import", {
        customer_ids: [...selected],
        bucket_date: format(date, "yyyy-MM-dd"),
      })
      const count = res.imported.length
      void queryClient.invalidateQueries({ queryKey: ["customers"] })
      toast.success(`Imported ${count} account${count === 1 ? "" : "s"}`)
      handleOpenChange(false)
    } catch (err) {
      toast.error(apiErrorDetail(err, "Could not import accounts"))
    } finally {
      setImporting(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Import from CustomerDaisy</DialogTitle>
          <DialogDescription>
            Pick the CustomerDaisy accounts to bring into DashManager.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          {recentQuery.isLoading ? (
            <div className="flex items-center justify-center gap-2 py-10 text-sm text-muted-foreground">
              <LoaderCircle className="size-4 animate-spin" />
              Loading recent accounts…
            </div>
          ) : recentQuery.isError ? (
            <p className="py-6 text-center text-sm text-destructive">
              Could not load CustomerDaisy accounts.
            </p>
          ) : accounts.length === 0 ? (
            <p className="py-6 text-center text-sm text-muted-foreground">
              No recent CustomerDaisy accounts available.
            </p>
          ) : (
            <div className="max-h-72 space-y-1 overflow-y-auto pr-1">
              {accounts.map((c) => {
                const checked = selected.has(c.customer_id)
                const noToken = !c.number_token
                return (
                  <label
                    key={c.customer_id}
                    className={cn(
                      "flex cursor-pointer items-start gap-3 rounded-lg border px-3 py-2.5 transition-colors",
                      checked
                        ? "border-primary/40 bg-primary/5"
                        : "border-border hover:bg-accent/50",
                    )}
                  >
                    <Checkbox
                      checked={checked}
                      onCheckedChange={(value) => toggle(c.customer_id, value)}
                      className="mt-0.5"
                      aria-label={`Select ${daisyName(c)}`}
                    />
                    <div className="min-w-0 flex-1 space-y-0.5">
                      <p className="truncate text-sm font-medium">{daisyName(c)}</p>
                      {c.email ? (
                        <p className="truncate text-xs text-muted-foreground">
                          {c.email}
                        </p>
                      ) : null}
                      <p className="text-xs text-muted-foreground">
                        {[c.phone, format(parseDbTimestamp(c.created_at), "MMM d, yyyy")]
                          .filter(Boolean)
                          .join(" · ")}
                      </p>
                      {c.full_address ? (
                        <p className="flex items-start gap-1 text-xs text-muted-foreground">
                          <MapPin className="mt-0.5 size-3 shrink-0" />
                          <span className="truncate">{c.full_address}</span>
                        </p>
                      ) : null}
                      {noToken ? (
                        <p className="flex items-center gap-1 text-xs text-status-warning-fg">
                          <CircleAlert className="size-3" />
                          No saved number — can't fetch OTP or log in later
                        </p>
                      ) : null}
                    </div>
                  </label>
                )
              })}
            </div>
          )}

          <div className="space-y-2">
            <Label htmlFor="import-daisy-date">Bucket date</Label>
            <Popover open={dateOpen} onOpenChange={setDateOpen}>
              <PopoverTrigger
                render={
                  <Button
                    id="import-daisy-date"
                    variant="outline"
                    className="w-full justify-between font-normal"
                  />
                }
              >
                {format(date, "EEE, MMM d yyyy")}
                <CalendarDays className="text-muted-foreground" />
              </PopoverTrigger>
              <PopoverContent align="start" className="w-auto p-0">
                <Calendar
                  mode="single"
                  selected={date}
                  defaultMonth={date}
                  onSelect={(d) => {
                    if (d) {
                      setDate(d)
                      setDateOpen(false)
                    }
                  }}
                />
              </PopoverContent>
            </Popover>
          </div>
        </div>

        <DialogFooter>
          <DialogClose render={<Button variant="outline" />}>Cancel</DialogClose>
          <Button
            onClick={() => void doImport()}
            disabled={selected.size === 0 || importing}
          >
            {importing ? (
              <LoaderCircle data-icon="inline-start" className="animate-spin" />
            ) : (
              <Download data-icon="inline-start" />
            )}
            Import {selected.size > 0 ? selected.size : ""} selected
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
