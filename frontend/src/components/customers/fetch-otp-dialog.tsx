import { useEffect, useRef, useState } from "react"
import { CircleAlert, Copy, LoaderCircle, RefreshCw, Smartphone } from "lucide-react"
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
import type { Customer } from "@/lib/types"
import { apiErrorDetail, customerName } from "./helpers"

type Phase = "fetching" | "got" | "timeout" | "failed"

interface OtpResult {
  code: string
  sms_text: string
  timeout?: boolean
}

/**
 * Fetch a fresh OTP for a customer so the user can type it into DoorDash on
 * their phone. The POST blocks up to ~2 min while the backend waits for an SMS.
 */
export function FetchOtpDialog({
  customer,
  onClose,
}: {
  customer: Customer
  onClose: () => void
}) {
  const [phase, setPhase] = useState<Phase>("fetching")
  const [result, setResult] = useState<OtpResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  /** Ignore a slow response that lands after the user re-fetched. */
  const attemptRef = useRef(0)

  async function fetchOtp() {
    const attempt = ++attemptRef.current
    setPhase("fetching")
    setError(null)
    try {
      const res = await api.post<OtpResult>(`/customers/${customer.id}/fetch-otp`)
      if (attempt !== attemptRef.current) return
      setResult(res)
      setPhase(res.timeout || !res.code ? "timeout" : "got")
    } catch (err) {
      if (attempt !== attemptRef.current) return
      setError(apiErrorDetail(err, "Could not fetch a code"))
      if (err instanceof ApiError && err.status === 400) {
        setError(
          apiErrorDetail(
            err,
            "This customer has no saved number — it wasn't created via the account flow.",
          ),
        )
      }
      setPhase("failed")
    }
  }

  // Kick off the (blocking) fetch as soon as the dialog mounts.
  useEffect(() => {
    void fetchOtp()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function copyCode() {
    if (!result?.code) return
    try {
      await navigator.clipboard.writeText(result.code)
      toast.success("Copied")
    } catch {
      toast.error("Could not copy to clipboard")
    }
  }

  return (
    <Dialog
      open
      onOpenChange={(next) => {
        if (!next) onClose()
      }}
    >
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Fetch OTP</DialogTitle>
          <DialogDescription>
            A fresh code for{" "}
            <span className="font-medium text-foreground">{customerName(customer)}</span>{" "}
            to type into DoorDash on the phone.
          </DialogDescription>
        </DialogHeader>

        {phase === "fetching" ? (
          <div className="flex flex-col items-center gap-4 py-8 text-center">
            <LoaderCircle className="size-8 animate-spin text-primary" />
            <div className="space-y-1">
              <p className="text-sm font-medium">
                Fetching a fresh code for {customerName(customer)}…
              </p>
              <p className="text-xs text-balance text-muted-foreground">
                This can take up to ~2 min while the SMS arrives.
              </p>
            </div>
          </div>
        ) : null}

        {phase === "got" && result ? (
          <div className="flex flex-col items-center gap-4 py-4 text-center">
            <p className="font-mono text-4xl font-bold tracking-wide text-primary tabular-nums">
              {result.code}
            </p>
            <Button variant="outline" size="sm" onClick={() => void copyCode()}>
              <Copy data-icon="inline-start" />
              Copy
            </Button>
            {customer.phone ? (
              <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
                <Smartphone className="size-3" />
                Sent to {customer.phone}
              </p>
            ) : null}
            {result.sms_text ? (
              <p className="max-w-xs text-xs text-balance text-muted-foreground">
                {result.sms_text}
              </p>
            ) : null}
          </div>
        ) : null}

        {phase === "timeout" ? (
          <Alert className="border-status-warning/25 bg-status-warning/10 text-status-warning-fg">
            <CircleAlert />
            <AlertTitle>No code arrived yet</AlertTitle>
            <AlertDescription className="text-status-warning-fg/90">
              Try Fetch again, or resend the code on the device.
            </AlertDescription>
          </Alert>
        ) : null}

        {phase === "failed" ? (
          <Alert variant="destructive">
            <CircleAlert />
            <AlertTitle>Could not fetch a code</AlertTitle>
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        ) : null}

        <DialogFooter>
          <DialogClose render={<Button variant="outline" />}>Close</DialogClose>
          {phase !== "fetching" && phase !== "failed" ? (
            <Button onClick={() => void fetchOtp()}>
              <RefreshCw data-icon="inline-start" />
              Fetch again
            </Button>
          ) : null}
          {phase === "failed" ? (
            <Button onClick={() => void fetchOtp()}>
              <RefreshCw data-icon="inline-start" />
              Try again
            </Button>
          ) : null}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
