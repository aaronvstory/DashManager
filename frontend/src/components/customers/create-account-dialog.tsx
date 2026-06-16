import { useEffect, useRef, useState, type ReactNode } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { format } from "date-fns"
import {
  CalendarDays,
  ChevronLeft,
  CircleAlert,
  CircleCheck,
  LoaderCircle,
  MapPin,
  Phone,
  RefreshCw,
  Sparkles,
} from "lucide-react"
import { toast } from "sonner"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Calendar } from "@/components/ui/calendar"
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import { api, ApiError } from "@/lib/api"
import type { CreateAccountRequest } from "@/lib/types"
import {
  SETTINGS_QUERY_KEY,
  type AppSettings,
} from "@/components/settings/shared"
import { useRunStore } from "@/store/runStore"
import { cn } from "@/lib/utils"
import { apiErrorDetail } from "./helpers"
import { HeadlessToggle, useHeadlessOverride } from "./headless-toggle"
import { CopyValue } from "./copy-cell"
import { ResultsTable, type CreatedRow } from "./results-table"

/** Sentinel option value: reveal a free-text field for a typed custom address. */
const CUSTOM_ANCHOR = "__custom__"

type Phase =
  | "idle"
  | "confirm"
  | "starting"
  | "running"
  | "created"
  | "failed"

/** One row in the live progress list during account creation. */
type StepKey = "identity" | "number" | "signup" | "otp" | "account"
type StepStatus = "pending" | "active" | "done"

const STEP_ORDER: ReadonlyArray<{ key: StepKey; label: string }> = [
  { key: "identity", label: "Generating identity" },
  { key: "number", label: "Renting phone number" },
  { key: "signup", label: "Submitting signup" },
  { key: "otp", label: "Waiting for SMS code" },
  { key: "account", label: "Creating account" },
]

interface DaisyLocation {
  index: number
  name: string
  city: string
  state: string
  full_address: string
}

interface LocationsResponse {
  locations: DaisyLocation[]
  balance: number
}

interface AnchorAddress {
  name?: string
  full_address: string
  city?: string
  state?: string
}

interface AnchorResponse {
  addresses: AnchorAddress[]
}

interface GeneratedIdentity {
  first_name: string
  last_name: string
  email: string
  city: string
  state: string
  full_address: string
}

interface RentedNumber {
  phone_number: string
  price: number | null
}

function str(v: unknown, fallback = ""): string {
  return typeof v === "string" ? v : fallback
}

function num(v: unknown): number | null {
  return typeof v === "number" ? v : null
}

export function CreateAccountDialog({
  open,
  onOpenChange,
  initialBatch,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** When set, the dialog ADDS account(s) to this existing batch (join, not
      mint) — pre-fills the label and sends batch_id on submit. */
  initialBatch?: { batch_id: string; batch_label: string }
}) {
  const queryClient = useQueryClient()
  const lastEvent = useRunStore((s) => s.lastEvent)

  const [phase, setPhase] = useState<Phase>("idle")
  const [date, setDate] = useState<Date>(() => new Date())
  const [dateOpen, setDateOpen] = useState(false)
  const [location, setLocation] = useState<string>("")
  const [customAddress, setCustomAddress] = useState<string>("")
  const [radius, setRadius] = useState<string>("5")
  const [count, setCount] = useState<string>("1")
  const [unique, setUnique] = useState<boolean>(true)
  const [batchLabel, setBatchLabel] = useState<string>("")
  const [batchInfo, setBatchInfo] = useState<{ index: number; of: number; created: number } | null>(null)
  const { headless, setHeadless } = useHeadlessOverride()

  // Live progress, built up from SSE events.
  const [steps, setSteps] = useState<Record<StepKey, StepStatus>>({
    identity: "pending",
    number: "pending",
    signup: "pending",
    otp: "pending",
    account: "pending",
  })
  const [identity, setIdentity] = useState<GeneratedIdentity | null>(null)
  const [rented, setRented] = useState<RentedNumber | null>(null)
  const [otpCode, setOtpCode] = useState<string | null>(null)
  const [otpResent, setOtpResent] = useState(false)
  // Append-only across a batch — the results table accumulates every account.
  const [results, setResults] = useState<CreatedRow[]>([])
  // Human-readable "✅ [n/N] Name · email · phone" lines shown live as a log.
  const [liveLines, setLiveLines] = useState<string[]>([])
  const [error, setError] = useState<string | null>(null)

  /** Ignore SSE events that predate this creation attempt. */
  const baselineEventId = useRef(-1)

  // Pull Daisy locations + balance when the dialog opens (subprocess: ~1-3s).
  const locationsQuery = useQuery({
    queryKey: ["daisy-locations"],
    queryFn: () => api.get<LocationsResponse>("/customers/daisy/locations"),
    enabled: open,
    staleTime: 60_000,
  })
  const locations = locationsQuery.data?.locations ?? []
  const balance = locationsQuery.data?.balance ?? null

  // The user's own anchor-address pool (my_addresses.json), offered alongside
  // the predefined locations so a batch can be anchored to a saved address.
  const anchorsQuery = useQuery({
    queryKey: ["daisy-addresses"],
    queryFn: () => api.get<AnchorResponse>("/daisy/addresses"),
    enabled: open,
    staleTime: 60_000,
  })
  const anchors = (anchorsQuery.data?.addresses ?? []).filter(
    (a) => a.full_address,
  )

  // Daisy settings supply the default email-inbox password — prefill it (the
  // user can override per batch). Display/prefill only; no write-back here.
  const settingsQuery = useQuery({
    queryKey: SETTINGS_QUERY_KEY,
    queryFn: () => api.get<AppSettings>("/settings"),
    enabled: open,
    staleTime: 60_000,
  })
  // The email-inbox password CustomerDaisy will use for each new account. It's
  // a server-side setting (not a per-request field), so this is shown read-only
  // for transparency — change it on the Settings page, not here.
  const defaultPassword = settingsQuery.data?.daisy.default_password ?? ""

  // Default the location to the first option (Edenton) once they load.
  useEffect(() => {
    if (!location && locations.length > 0) {
      setLocation(locations[0].full_address)
    }
  }, [location, locations])

  // When opened to ADD to an existing batch, pre-fill the label so the user
  // sees which batch they're adding to (the batch_id is sent on submit).
  useEffect(() => {
    if (open && initialBatch) setBatchLabel(initialBatch.batch_label)
  }, [open, initialBatch])

  useEffect(() => {
    if (!lastEvent) return
    if (phase !== "starting" && phase !== "running") return
    if (lastEvent.id <= baselineEventId.current) return

    const d = lastEvent.data

    switch (lastEvent.type) {
      case "batch_started":
        setBatchInfo({ index: 0, of: num(d.of) ?? num(d.count) ?? 1, created: 0 })
        break
      case "batch_progress":
        setBatchInfo({
          index: num(d.index) ?? 0,
          of: num(d.of) ?? 1,
          created: num(d.created) ?? 0,
        })
        // each new account in the batch restarts the per-account step list —
        // but KEEP batchInfo (we just set it above; clearing it would make the
        // next account_created/account_failed think this is a single-account
        // run and flip the dialog to a terminal state mid-batch).
        resetProgress({ keepBatch: true })
        setPhase("running")
        break
      case "batch_done":
        void queryClient.invalidateQueries({ queryKey: ["customers"] })
        toast.success(
          `Batch done: ${num(d.created) ?? 0}/${num(d.of) ?? 0} created`,
        )
        setPhase("created")
        break
      case "identity_generating":
        setPhase("running")
        setSteps((s) => ({ ...s, identity: "active" }))
        break
      case "identity_generated":
        setPhase("running")
        setIdentity({
          first_name: str(d.first_name),
          last_name: str(d.last_name),
          email: str(d.email),
          city: str(d.city),
          state: str(d.state),
          full_address: str(d.full_address),
        })
        setSteps((s) => ({ ...s, identity: "done" }))
        break
      case "number_renting":
        setSteps((s) => ({ ...s, number: "active" }))
        break
      case "number_rented":
        setRented({
          phone_number: str(d.phone_number),
          price: num(d.price),
        })
        setSteps((s) => ({ ...s, number: "done" }))
        break
      case "signup_submitting":
        setSteps((s) => ({ ...s, signup: "active" }))
        break
      case "otp_waiting":
        setSteps((s) => ({ ...s, signup: "done", otp: "active" }))
        break
      case "otp_received":
        setOtpCode(str(d.code))
        setSteps((s) => ({ ...s, otp: "done" }))
        break
      case "otp_resent":
        setOtpResent(true)
        break
      case "account_created": {
        const row: CreatedRow = {
          customer_id: num(d.customer_id) ?? -1,
          name: str(d.name),
          email: str(d.email),
          email_password: str(d.email_password),
          phone: str(d.phone),
          daisy_id: str(d.daisy_id),
          full_address: str(d.full_address),
          dist_from_anchor: num(d.dist_from_anchor),
          bucket_date: str(d.bucket_date),
        }
        // Append (never overwrite) so a batch accumulates every account.
        setResults((rs) => [...rs, row])
        const nOf = batchInfo && batchInfo.of > 1
          ? `[${batchInfo.index}/${batchInfo.of}] `
          : ""
        setLiveLines((ls) => [
          ...ls,
          `✅ ${nOf}${row.name || "new customer"} · ${row.email} · ${row.phone}`,
        ])
        setSteps({
          identity: "done",
          number: "done",
          signup: "done",
          otp: "done",
          account: "done",
        })
        void queryClient.invalidateQueries({ queryKey: ["customers"] })
        toast.success(`Account created for ${str(d.name) || "new customer"}`)
        // In a batch, batch_done flips to "created"; for a single account flip
        // now. (batchInfo is set only during a multi-account batch.)
        if (!batchInfo || batchInfo.of <= 1) {
          setPhase("created")
        }
        break
      }
      case "account_failed":
        // a single failure in a batch is non-fatal — keep running; only fail the
        // whole dialog for a single (non-batch) creation.
        if (!batchInfo || batchInfo.of <= 1) {
          setError(str(d.error) || "Account creation failed.")
          setPhase("failed")
        }
        break
      default:
        break
    }
  }, [lastEvent, phase, queryClient, batchInfo])

  function resetProgress(opts?: { keepBatch?: boolean }) {
    setSteps({
      identity: "pending",
      number: "pending",
      signup: "pending",
      otp: "pending",
      account: "pending",
    })
    setIdentity(null)
    setRented(null)
    setOtpCode(null)
    setOtpResent(false)
    setError(null)
    // Per-account resets (mid-batch) preserve batch context AND the accumulated
    // results/log — clearing them on keepBatch would leave only the last
    // account in the table. A full reset (close/idle) clears everything.
    if (!opts?.keepBatch) {
      setBatchInfo(null)
      setResults([])
      setLiveLines([])
    }
  }

  function resetToIdle() {
    resetProgress()
    setPhase("idle")
  }

  function handleOpenChange(next: boolean) {
    onOpenChange(next)
    if (!next) {
      resetToIdle()
      // Clear the batch label so a re-open with no initialBatch starts blank
      // (an add-to-batch label must not carry into a later plain create).
      setBatchLabel("")
      setCustomAddress("")
      setDate(new Date())
      setDateOpen(false)
    }
  }

  /** The bare anchor address to send, resolving the 3 picker sources:
      predefined location, "anchor:"-prefixed saved address, or a typed custom
      address (the CUSTOM_ANCHOR sentinel). */
  function resolvedOrigin(): string {
    if (location === CUSTOM_ANCHOR) return customAddress.trim()
    if (location.startsWith("anchor:")) return location.slice("anchor:".length)
    return location
  }

  async function start() {
    resetProgress()
    setPhase("starting")
    baselineEventId.current = useRunStore.getState().lastEvent?.id ?? -1
    setBatchInfo(null)
    const radiusMiles = Number(radius)
    const n = Math.max(1, Math.floor(Number(count) || 1))
    const origin = resolvedOrigin()
    const body: CreateAccountRequest = {
      bucket_date: format(date, "yyyy-MM-dd"),
      location_origin: origin || undefined,
      radius_miles: Number.isFinite(radiusMiles) ? radiusMiles : undefined,
      count: n,
      unique,
      batch_label: batchLabel.trim() || undefined,
      // Join the existing batch when the dialog was opened for that.
      batch_id: initialBatch?.batch_id || undefined,
    }
    if (headless !== null) body.headless = headless
    try {
      await api.post<{ started: boolean }>("/customers/create-account", body)
    } catch (err) {
      setPhase("idle")
      if (err instanceof ApiError && err.status === 409) {
        toast.error("An account creation is already running")
      } else {
        toast.error(apiErrorDetail(err, "Could not start account creation"))
      }
    }
  }

  const busy = phase === "starting" || phase === "running"
  const lowBalance = balance !== null && balance < 0.5

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent
        className={cn(
          // The results table needs room; the form/progress views stay compact.
          phase === "created" ? "sm:max-w-3xl" : "sm:max-w-md",
        )}
      >
        <DialogHeader>
          <DialogTitle>Create account</DialogTitle>
          <DialogDescription>
            Sign up a brand-new DoorDash account automatically with a generated
            identity.
          </DialogDescription>
        </DialogHeader>

        {phase === "idle" ? (
          <>
            <div className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="create-account-date">Bucket date</Label>
                <Popover open={dateOpen} onOpenChange={setDateOpen}>
                  <PopoverTrigger
                    render={
                      <Button
                        id="create-account-date"
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

              <div className="space-y-2">
                <Label htmlFor="create-account-location">Anchor address</Label>
                {locationsQuery.isLoading ? (
                  <div className="flex h-8 items-center gap-2 px-1 text-sm text-muted-foreground">
                    <LoaderCircle className="size-4 animate-spin" />
                    Loading anchors…
                  </div>
                ) : locationsQuery.isError ? (
                  <p className="text-sm text-destructive">
                    Could not load anchors.
                  </p>
                ) : (
                  <Select
                    items={[
                      ...locations.map((l) => ({
                        label: `${l.name} — ${l.city}, ${l.state}`,
                        value: l.full_address,
                      })),
                      ...anchors.map((a) => ({
                        label: a.name
                          ? `★ ${a.name} — ${a.full_address}`
                          : `★ ${a.full_address}`,
                        // Namespace so an anchor whose address equals a
                        // predefined location's isn't deduped (and made
                        // unselectable) by the Select's value map. Stripped on
                        // submit.
                        value: `anchor:${a.full_address}`,
                      })),
                      { label: "✎ Custom address…", value: CUSTOM_ANCHOR },
                    ]}
                    value={location}
                    onValueChange={(v) => {
                      if (v) setLocation(v as string)
                    }}
                  >
                    <SelectTrigger
                      id="create-account-location"
                      className="w-full"
                    >
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {/* Source 1: the predefined CustomerDaisy anchors. */}
                      <SelectGroup>
                        <SelectLabel>Predefined anchors</SelectLabel>
                        {locations.map((l) => (
                          <SelectItem key={l.index} value={l.full_address}>
                            {l.name} — {l.city}, {l.state}
                          </SelectItem>
                        ))}
                      </SelectGroup>
                      {/* Source 2: the user's own saved anchor pool
                          (my_addresses.json), managed in My Anchors. */}
                      {anchors.length > 0 ? (
                        <SelectGroup>
                          <SelectLabel>My anchors</SelectLabel>
                          {anchors.map((a) => (
                            <SelectItem
                              key={`anchor:${a.full_address}`}
                              value={`anchor:${a.full_address}`}
                            >
                              ★ {a.name ? `${a.name} — ` : ""}
                              {a.full_address}
                            </SelectItem>
                          ))}
                        </SelectGroup>
                      ) : null}
                      {/* Source 3: a one-off typed address. */}
                      <SelectGroup>
                        <SelectLabel>Custom</SelectLabel>
                        <SelectItem value={CUSTOM_ANCHOR}>
                          ✎ Custom address…
                        </SelectItem>
                      </SelectGroup>
                    </SelectContent>
                  </Select>
                )}
                {location === CUSTOM_ANCHOR ? (
                  <Input
                    aria-label="Custom anchor address"
                    placeholder="123 Main St, City, ST 00000"
                    value={customAddress}
                    onChange={(e) => setCustomAddress(e.target.value)}
                  />
                ) : null}
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-2">
                  <Label htmlFor="create-account-radius">Radius (miles)</Label>
                  <Input
                    id="create-account-radius"
                    type="number"
                    min={1}
                    value={radius}
                    onChange={(e) => setRadius(e.target.value)}
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="create-account-count">How many</Label>
                  <Input
                    id="create-account-count"
                    type="number"
                    min={1}
                    max={20}
                    value={count}
                    onChange={(e) => setCount(e.target.value)}
                  />
                </div>
              </div>

              <div className="flex items-start justify-between gap-3 rounded-lg border border-border px-3 py-2.5">
                <div className="space-y-0.5">
                  <Label htmlFor="create-account-unique">
                    Unique address per account
                  </Label>
                  <p className="text-xs text-muted-foreground">
                    {unique
                      ? "Each account gets its own address within the radius."
                      : "All accounts share one address near the anchor."}
                  </p>
                </div>
                <Switch
                  id="create-account-unique"
                  checked={unique}
                  onCheckedChange={setUnique}
                />
              </div>

              <div className="space-y-2">
                <Label htmlFor="create-account-email-pw">Email password</Label>
                <Input
                  id="create-account-email-pw"
                  readOnly
                  value={
                    settingsQuery.isLoading ? "Loading…" : defaultPassword
                  }
                  className="text-muted-foreground"
                />
                <p className="text-xs text-muted-foreground">
                  From CustomerDaisy settings; used for each new inbox. Change it
                  on the Settings page.
                </p>
              </div>

              {Number(count) > 1 ? (
                <div className="space-y-2">
                  <Label htmlFor="create-account-batch">Batch label</Label>
                  <Input
                    id="create-account-batch"
                    placeholder="e.g. June group"
                    value={batchLabel}
                    onChange={(e) => setBatchLabel(e.target.value)}
                  />
                  <p className="text-xs text-muted-foreground">
                    Saved in CustomerDaisy as
                    <span className="num"> “{(batchLabel.trim() || "batch …")} - claude”</span>
                    {" "}— grab the OTPs from the Batch OTP page.
                  </p>
                </div>
              ) : null}

              {balance !== null ? (
                <p
                  className={cn(
                    "text-xs",
                    lowBalance ? "text-amber-500" : "text-muted-foreground",
                  )}
                >
                  api.cc balance: ${balance.toFixed(2)}
                </p>
              ) : null}

              <Alert>
                <Sparkles />
                <AlertTitle>Automatic account creation</AlertTitle>
                <AlertDescription>
                  A Chrome window opens and signs up fresh DoorDash account(s)
                  with a generated identity, email, phone, and address near the
                  chosen location; the SMS code is entered automatically.
                  <strong className="mt-1 block text-amber-500">
                    Uses real mouse/keyboard to pass bot detection — don’t touch
                    the PC while it runs.
                  </strong>
                </AlertDescription>
              </Alert>

              <HeadlessToggle headless={headless} onChange={setHeadless} />
            </div>

            <DialogFooter>
              <DialogClose render={<Button variant="outline" />}>
                Cancel
              </DialogClose>
              <Button
                onClick={() => setPhase("confirm")}
                disabled={
                  locationsQuery.isLoading ||
                  (location === CUSTOM_ANCHOR && !customAddress.trim())
                }
              >
                <Sparkles data-icon="inline-start" />
                Review
              </Button>
            </DialogFooter>
          </>
        ) : null}

        {phase === "confirm" ? (
          <>
            <div className="space-y-3">
              <p className="text-sm text-muted-foreground">
                Review the generation settings, then proceed.
              </p>
              <dl className="divide-y divide-border rounded-lg border border-border text-sm">
                <SettingRow label="Anchor">
                  <CopyValue value={resolvedOrigin()} label="anchor address" />
                </SettingRow>
                <SettingRow label="Radius">
                  <span className="num">{radius} mi</span>
                </SettingRow>
                <SettingRow label="Addresses">
                  {unique ? "Unique per account" : "Shared (one address)"}
                </SettingRow>
                <SettingRow label="How many">
                  <span className="num">
                    {Math.max(1, Math.floor(Number(count) || 1))}
                  </span>
                </SettingRow>
                <SettingRow label="Email PW">
                  <CopyValue value={defaultPassword} label="email password" mono />
                </SettingRow>
                <SettingRow label="Bucket">
                  <span className="num">{format(date, "yyyy-MM-dd")}</span>
                </SettingRow>
              </dl>
              {balance !== null ? (
                <p
                  className={cn(
                    "text-xs",
                    lowBalance ? "text-amber-500" : "text-muted-foreground",
                  )}
                >
                  api.cc balance: ${balance.toFixed(2)}
                </p>
              ) : null}
            </div>
            <DialogFooter>
              <Button variant="outline" onClick={() => setPhase("idle")}>
                <ChevronLeft data-icon="inline-start" />
                Back
              </Button>
              <Button onClick={() => void start()}>
                <Sparkles data-icon="inline-start" />
                Looks good — proceed
              </Button>
            </DialogFooter>
          </>
        ) : null}

        {busy ? (
          <>
            {batchInfo && batchInfo.of > 1 ? (
              <div className="flex items-center justify-between border border-border bg-card px-3 py-2 text-sm">
                <span className="font-medium">
                  Account <span className="num">{batchInfo.index}</span> of{" "}
                  <span className="num">{batchInfo.of}</span>
                </span>
                <span className="num text-xs text-emerald-500">
                  {batchInfo.created} created
                </span>
              </div>
            ) : null}
            <div className="py-2">
              <StepList
                steps={steps}
                identity={identity}
                rented={rented}
                otpCode={otpCode}
                otpResent={otpResent}
              />
            </div>
            {liveLines.length > 0 ? (
              <div className="max-h-32 space-y-0.5 overflow-y-auto rounded-lg border border-border bg-card px-3 py-2 text-xs">
                {liveLines.map((line, i) => (
                  <p key={i} className="text-muted-foreground">
                    {line}
                  </p>
                ))}
              </div>
            ) : null}
            <DialogFooter>
              <DialogClose render={<Button variant="outline" />}>
                Hide — creation keeps running
              </DialogClose>
            </DialogFooter>
          </>
        ) : null}

        {phase === "created" ? (
          <>
            <div className="flex items-center gap-2 py-1 text-sm font-medium text-emerald-500">
              <CircleCheck className="size-5" />
              {results.length === 1
                ? "Account created"
                : `${results.length} accounts created`}
            </div>
            <ResultsTable rows={results} />
            <DialogFooter>
              <Button variant="outline" onClick={resetToIdle}>
                Create another
              </Button>
              <DialogClose render={<Button />}>Done</DialogClose>
            </DialogFooter>
          </>
        ) : null}

        {phase === "failed" ? (
          <>
            <Alert variant="destructive">
              <CircleAlert />
              <AlertTitle>Account creation failed</AlertTitle>
              <AlertDescription>{error}</AlertDescription>
            </Alert>
            <DialogFooter>
              <DialogClose render={<Button variant="outline" />}>
                Close
              </DialogClose>
              <Button onClick={() => void start()}>
                <RefreshCw data-icon="inline-start" />
                Retry
              </Button>
            </DialogFooter>
          </>
        ) : null}
      </DialogContent>
    </Dialog>
  )
}

/** One label/value row in the confirm-step generation-settings card. */
function SettingRow({
  label,
  children,
}: {
  label: string
  children: ReactNode
}) {
  return (
    <div className="flex items-center justify-between gap-3 px-3 py-2">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="min-w-0 truncate text-right font-medium">{children}</dd>
    </div>
  )
}

function StepList({
  steps,
  identity,
  rented,
  otpCode,
  otpResent,
}: {
  steps: Record<StepKey, StepStatus>
  identity: GeneratedIdentity | null
  rented: RentedNumber | null
  otpCode: string | null
  otpResent: boolean
}) {
  return (
    <ol className="relative space-y-1">
      {STEP_ORDER.map((step, i) => {
        const status = steps[step.key]
        const isLast = i === STEP_ORDER.length - 1
        return (
          <li key={step.key} className="relative">
            {/* Connecting line between markers. */}
            {!isLast ? (
              <span
                aria-hidden
                className={cn(
                  "absolute top-7 left-[1.0625rem] h-[calc(100%-1rem)] w-px",
                  status === "done" ? "bg-emerald-500/40" : "bg-border",
                )}
              />
            ) : null}
            <div
              className={cn(
                "flex items-start gap-3 rounded-lg px-2 py-2 transition-colors",
                status === "active" ? "bg-primary/5" : "",
              )}
            >
              <span className="mt-0.5 flex size-[1.375rem] shrink-0 items-center justify-center">
                {status === "done" ? (
                  <CircleCheck className="size-[1.375rem] text-emerald-500" />
                ) : status === "active" ? (
                  <LoaderCircle className="size-[1.375rem] animate-spin text-primary" />
                ) : (
                  <span className="size-2 rounded-full bg-muted-foreground/40" />
                )}
              </span>
              <div className="min-w-0 flex-1 space-y-1">
                <p
                  className={cn(
                    "text-sm leading-[1.375rem] transition-colors",
                    status === "pending"
                      ? "text-muted-foreground"
                      : "font-medium text-foreground",
                  )}
                >
                  {step.key === "otp" && status === "active"
                    ? "Waiting for SMS code…"
                    : step.label}
                </p>

                {/* Inline details revealed as events arrive. */}
                {step.key === "identity" && identity ? (
                  <div className="space-y-0.5 text-xs text-muted-foreground">
                    <p className="font-medium text-foreground/80">
                      {`${identity.first_name} ${identity.last_name}`.trim()}
                    </p>
                    {identity.email ? <p>{identity.email}</p> : null}
                    {identity.city || identity.state ? (
                      <p className="flex items-center gap-1">
                        <MapPin className="size-3" />
                        {[identity.city, identity.state]
                          .filter(Boolean)
                          .join(", ")}
                      </p>
                    ) : null}
                  </div>
                ) : null}

                {step.key === "number" && rented ? (
                  <p className="flex items-center gap-1 text-xs text-muted-foreground">
                    <Phone className="size-3" />
                    {rented.phone_number}
                    {rented.price !== null
                      ? ` · $${rented.price.toFixed(2)}`
                      : ""}
                  </p>
                ) : null}

                {step.key === "otp" && otpResent && !otpCode ? (
                  <p className="text-xs text-amber-500">code resent</p>
                ) : null}

                {step.key === "otp" && otpCode ? (
                  <p className="text-xs text-muted-foreground">
                    code {otpCode}
                  </p>
                ) : null}
              </div>
            </div>
          </li>
        )
      })}
    </ol>
  )
}
