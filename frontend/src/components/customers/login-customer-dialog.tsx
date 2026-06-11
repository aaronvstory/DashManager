import { useEffect, useRef, useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { CircleAlert, CircleCheck, LoaderCircle, LogIn, RefreshCw } from "lucide-react"
import { toast } from "sonner"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { api, ApiError } from "@/lib/api"
import type { Customer, ReloginRequest } from "@/lib/types"
import { useRunStore } from "@/store/runStore"
import { cn } from "@/lib/utils"
import { apiErrorDetail, customerName } from "./helpers"
import { HeadlessToggle, useHeadlessOverride } from "./headless-toggle"

type Phase = "idle" | "starting" | "running" | "done" | "failed"

/** One row in the live progress list during re-login. */
type StepKey = "browser" | "otp" | "code" | "loggedin"
type StepStatus = "pending" | "active" | "done"

const STEP_ORDER: ReadonlyArray<{ key: StepKey; label: string }> = [
  { key: "browser", label: "Opening browser" },
  { key: "otp", label: "Waiting for SMS code" },
  { key: "code", label: "Code received" },
  { key: "loggedin", label: "Logged in" },
]

function str(v: unknown, fallback = ""): string {
  return typeof v === "string" ? v : fallback
}

function num(v: unknown): number | null {
  return typeof v === "number" ? v : null
}

export function LoginCustomerDialog({
  customer,
  onClose,
}: {
  customer: Customer
  onClose: () => void
}) {
  const queryClient = useQueryClient()
  const lastEvent = useRunStore((s) => s.lastEvent)

  const [phase, setPhase] = useState<Phase>("idle")
  const [steps, setSteps] = useState<Record<StepKey, StepStatus>>({
    browser: "pending",
    otp: "pending",
    code: "pending",
    loggedin: "pending",
  })
  const [otpCode, setOtpCode] = useState<string | null>(null)
  const [otpResent, setOtpResent] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const { headless, setHeadless } = useHeadlessOverride()

  /** Ignore SSE events that predate this login attempt. */
  const baselineEventId = useRef(-1)

  useEffect(() => {
    if (!lastEvent) return
    if (phase !== "starting" && phase !== "running") return
    if (lastEvent.id <= baselineEventId.current) return

    const d = lastEvent.data
    // Events that carry a customer_id must match this row; otp_* events carry
    // none — only one login runs at a time, so accept them once we're running.
    const evCustomerId = num(d.customer_id)
    if (evCustomerId !== null && evCustomerId !== customer.id) return

    switch (lastEvent.type) {
      case "relogin_started":
        setPhase("running")
        setSteps((s) => ({ ...s, browser: "active" }))
        break
      case "otp_waiting":
        setSteps((s) => ({ ...s, browser: "done", otp: "active" }))
        break
      case "otp_received":
        setOtpCode(str(d.code))
        setSteps((s) => ({ ...s, otp: "done", code: "done" }))
        break
      case "otp_resent":
        setOtpResent(true)
        break
      case "relogin_outcome": {
        const outcome = str(d.outcome)
        if (outcome !== "logged_in") {
          setError(`Login did not complete: ${outcome || "unknown outcome"}`)
          setPhase("failed")
        }
        break
      }
      case "relogin_done":
        setSteps({
          browser: "done",
          otp: "done",
          code: "done",
          loggedin: "done",
        })
        setPhase("done")
        void queryClient.invalidateQueries({ queryKey: ["customers"] })
        toast.success(`Logged in ${customerName(customer)}`)
        break
      case "relogin_failed":
        setError(str(d.error) || "Login failed.")
        setPhase("failed")
        break
      default:
        break
    }
  }, [lastEvent, phase, customer, queryClient])

  function resetProgress() {
    setSteps({
      browser: "pending",
      otp: "pending",
      code: "pending",
      loggedin: "pending",
    })
    setOtpCode(null)
    setOtpResent(false)
    setError(null)
  }

  async function start() {
    resetProgress()
    setPhase("starting")
    baselineEventId.current = useRunStore.getState().lastEvent?.id ?? -1
    const body: ReloginRequest = {}
    if (headless !== null) body.headless = headless
    try {
      await api.post<{ started: boolean }>(
        `/customers/${customer.id}/relogin`,
        body,
      )
    } catch (err) {
      setPhase("idle")
      if (err instanceof ApiError && err.status === 409) {
        toast.error("A login is already running")
      } else {
        toast.error(apiErrorDetail(err, "Could not start the login"))
      }
    }
  }

  const busy = phase === "starting" || phase === "running"

  return (
    <Dialog
      open
      onOpenChange={(next) => {
        if (!next) onClose()
      }}
    >
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Log in</DialogTitle>
          <DialogDescription>
            Re-login{" "}
            <span className="font-medium text-foreground">{customerName(customer)}</span>{" "}
            with an automated headed browser. The SMS code is entered automatically.
          </DialogDescription>
        </DialogHeader>

        {phase === "idle" ? (
          <>
            <Alert>
              <LogIn />
              <AlertTitle>Automatic re-login</AlertTitle>
              <AlertDescription>
                A Chromium window opens and logs the customer back into DoorDash,
                refreshing the saved session.
              </AlertDescription>
            </Alert>
            <HeadlessToggle headless={headless} onChange={setHeadless} />
            <DialogFooter>
              <DialogClose render={<Button variant="outline" />}>Cancel</DialogClose>
              <Button onClick={() => void start()}>
                <LogIn data-icon="inline-start" />
                Start
              </Button>
            </DialogFooter>
          </>
        ) : null}

        {busy ? (
          <>
            <div className="py-2">
              <StepList steps={steps} otpCode={otpCode} otpResent={otpResent} />
            </div>
            <DialogFooter>
              <DialogClose render={<Button variant="outline" />}>
                Hide — login keeps running
              </DialogClose>
            </DialogFooter>
          </>
        ) : null}

        {phase === "done" ? (
          <>
            <div className="flex flex-col items-center gap-4 py-6 text-center">
              <div className="flex size-12 items-center justify-center rounded-full bg-emerald-500/10 ring-1 ring-emerald-500/25">
                <CircleCheck className="size-6 text-emerald-500" />
              </div>
              <div className="space-y-1">
                <p className="text-sm font-medium">Logged in</p>
                <p className="text-sm text-muted-foreground">{customerName(customer)}</p>
              </div>
            </div>
            <DialogFooter>
              <DialogClose render={<Button />}>Done</DialogClose>
            </DialogFooter>
          </>
        ) : null}

        {phase === "failed" ? (
          <>
            <Alert variant="destructive">
              <CircleAlert />
              <AlertTitle>Login failed</AlertTitle>
              <AlertDescription>{error}</AlertDescription>
            </Alert>
            <DialogFooter>
              <DialogClose render={<Button variant="outline" />}>Close</DialogClose>
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
  otpCode,
  otpResent,
}: {
  steps: Record<StepKey, StepStatus>
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

                {step.key === "otp" && otpResent && !otpCode ? (
                  <p className="text-xs text-amber-500">code resent</p>
                ) : null}

                {step.key === "code" && otpCode ? (
                  <p className="text-xs text-muted-foreground">code {otpCode}</p>
                ) : null}
              </div>
            </div>
          </li>
        )
      })}
    </ol>
  )
}
