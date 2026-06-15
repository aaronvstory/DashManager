import { useEffect, useRef, useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { format } from "date-fns"
import {
  CalendarDays,
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
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { api, ApiError } from "@/lib/api"
import type { CreateAccountRequest } from "@/lib/types"
import { useRunStore } from "@/store/runStore"
import { cn } from "@/lib/utils"
import { apiErrorDetail } from "./helpers"
import { HeadlessToggle, useHeadlessOverride } from "./headless-toggle"

type Phase = "idle" | "starting" | "running" | "created" | "failed"

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

interface CreatedAccount {
  customer_id: number
  name: string
  email: string
  phone: string
  bucket_date: string
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
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const queryClient = useQueryClient()
  const lastEvent = useRunStore((s) => s.lastEvent)

  const [phase, setPhase] = useState<Phase>("idle")
  const [date, setDate] = useState<Date>(() => new Date())
  const [dateOpen, setDateOpen] = useState(false)
  const [location, setLocation] = useState<string>("")
  const [radius, setRadius] = useState<string>("5")
  const [count, setCount] = useState<string>("1")
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
  const [created, setCreated] = useState<CreatedAccount | null>(null)
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

  // Default the location to the first option (Edenton) once they load.
  useEffect(() => {
    if (!location && locations.length > 0) {
      setLocation(locations[0].full_address)
    }
  }, [location, locations])

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
      case "account_created":
        setCreated({
          customer_id: num(d.customer_id) ?? -1,
          name: str(d.name),
          email: str(d.email),
          phone: str(d.phone),
          bucket_date: str(d.bucket_date),
        })
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
    setCreated(null)
    setError(null)
    // Per-account resets (mid-batch) preserve batch context; a full reset
    // (close/idle) clears it.
    if (!opts?.keepBatch) setBatchInfo(null)
  }

  function resetToIdle() {
    resetProgress()
    setPhase("idle")
  }

  function handleOpenChange(next: boolean) {
    onOpenChange(next)
    if (!next) {
      resetToIdle()
      setDate(new Date())
      setDateOpen(false)
    }
  }

  async function start() {
    resetProgress()
    setPhase("starting")
    baselineEventId.current = useRunStore.getState().lastEvent?.id ?? -1
    setBatchInfo(null)
    const radiusMiles = Number(radius)
    const n = Math.max(1, Math.floor(Number(count) || 1))
    const body: CreateAccountRequest = {
      bucket_date: format(date, "yyyy-MM-dd"),
      location_origin: location || undefined,
      radius_miles: Number.isFinite(radiusMiles) ? radiusMiles : undefined,
      count: n,
      batch_label: batchLabel.trim() || undefined,
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
      <DialogContent className="sm:max-w-md">
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
                <Label htmlFor="create-account-location">Location</Label>
                {locationsQuery.isLoading ? (
                  <div className="flex h-8 items-center gap-2 px-1 text-sm text-muted-foreground">
                    <LoaderCircle className="size-4 animate-spin" />
                    Loading locations…
                  </div>
                ) : locationsQuery.isError ? (
                  <p className="text-sm text-destructive">
                    Could not load locations.
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
                        value: a.full_address,
                      })),
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
                      {locations.map((l) => (
                        <SelectItem key={l.index} value={l.full_address}>
                          {l.name} — {l.city}, {l.state}
                        </SelectItem>
                      ))}
                      {/* The user's own saved addresses (my_addresses.json),
                          starred to distinguish them from predefined cities. */}
                      {anchors.map((a) => (
                        <SelectItem
                          key={`anchor:${a.full_address}`}
                          value={a.full_address}
                        >
                          ★ {a.name ? `${a.name} — ` : ""}
                          {a.full_address}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                )}
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
                onClick={() => void start()}
                disabled={locationsQuery.isLoading}
              >
                <Sparkles data-icon="inline-start" />
                Create account
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
            <DialogFooter>
              <DialogClose render={<Button variant="outline" />}>
                Hide — creation keeps running
              </DialogClose>
            </DialogFooter>
          </>
        ) : null}

        {phase === "created" ? (
          <>
            <div className="flex flex-col items-center gap-4 py-6 text-center">
              <div className="flex size-12 items-center justify-center rounded-full bg-emerald-500/10 ring-1 ring-emerald-500/25">
                <CircleCheck className="size-6 text-emerald-500" />
              </div>
              <div className="space-y-1">
                <p className="text-sm font-medium">Account created</p>
                <p className="text-sm text-muted-foreground">
                  {created?.name || "Customer"}
                  {created?.email ? ` · ${created.email}` : ""}
                </p>
                {created?.phone ? (
                  <p className="text-xs text-muted-foreground">
                    {created.phone}
                    {created.bucket_date ? ` · ${created.bucket_date}` : ""}
                  </p>
                ) : null}
              </div>
            </div>
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
