import { useEffect, useRef, useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { format } from "date-fns"
import {
  CalendarDays,
  CircleAlert,
  CircleCheck,
  LoaderCircle,
  Monitor,
  RefreshCw,
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
import { Label } from "@/components/ui/label"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { api, ApiError } from "@/lib/api"
import { useRunStore } from "@/store/runStore"
import { apiErrorDetail } from "./helpers"

type Phase = "idle" | "starting" | "waiting" | "captured" | "failed"

export function AddCustomerDialog({
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
  const [captured, setCaptured] = useState<{ name: string; email: string } | null>(null)
  const [error, setError] = useState<string | null>(null)
  /** Ignore SSE events that predate this capture attempt. */
  const baselineEventId = useRef(-1)

  useEffect(() => {
    if (!lastEvent) return
    if (phase !== "starting" && phase !== "waiting") return
    if (lastEvent.id <= baselineEventId.current) return

    if (lastEvent.type === "login_waiting") {
      setPhase("waiting")
    } else if (lastEvent.type === "login_captured") {
      const name = typeof lastEvent.data.name === "string" ? lastEvent.data.name : ""
      const email = typeof lastEvent.data.email === "string" ? lastEvent.data.email : ""
      setCaptured({ name, email })
      setPhase("captured")
      void queryClient.invalidateQueries({ queryKey: ["customers"] })
      toast.success(`Session captured for ${name || "new customer"}`)
    } else if (lastEvent.type === "login_failed") {
      setError(
        typeof lastEvent.data.error === "string" && lastEvent.data.error
          ? lastEvent.data.error
          : "Login capture failed.",
      )
      setPhase("failed")
    }
  }, [lastEvent, phase, queryClient])

  function resetToIdle() {
    setPhase("idle")
    setCaptured(null)
    setError(null)
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
    setError(null)
    setPhase("starting")
    baselineEventId.current = useRunStore.getState().lastEvent?.id ?? -1
    try {
      await api.post<{ started: boolean }>("/customers/login", {
        bucket_date: format(date, "yyyy-MM-dd"),
      })
    } catch (err) {
      setPhase("idle")
      if (err instanceof ApiError && err.status === 409) {
        toast.error("A login capture is already running")
      } else {
        toast.error(apiErrorDetail(err, "Could not start the login capture"))
      }
    }
  }

  const busy = phase === "starting" || phase === "waiting"

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Add customer</DialogTitle>
          <DialogDescription>
            Capture a DoorDash session by logging the customer in manually.
          </DialogDescription>
        </DialogHeader>

        {phase === "idle" ? (
          <>
            <div className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="add-customer-date">Bucket date</Label>
                <Popover open={dateOpen} onOpenChange={setDateOpen}>
                  <PopoverTrigger
                    render={
                      <Button
                        id="add-customer-date"
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
                <p className="text-xs text-muted-foreground">
                  The new customer is grouped under this date. Defaults to today.
                </p>
              </div>

              <Alert>
                <Monitor />
                <AlertTitle>Manual login capture</AlertTitle>
                <AlertDescription>
                  A Chromium window will open — log the customer into DoorDash; the app
                  captures the session and profile automatically.
                </AlertDescription>
              </Alert>
            </div>

            <DialogFooter>
              <DialogClose render={<Button variant="outline" />}>Cancel</DialogClose>
              <Button onClick={() => void start()}>Start login capture</Button>
            </DialogFooter>
          </>
        ) : null}

        {busy ? (
          <>
            <div className="flex flex-col items-center gap-4 py-8 text-center">
              <LoaderCircle className="size-8 animate-spin text-primary" />
              <div className="space-y-1">
                <p className="text-sm font-medium">
                  {phase === "starting"
                    ? "Opening the Chromium window…"
                    : "Waiting for login in the Chromium window…"}
                </p>
                <p className="text-xs text-balance text-muted-foreground">
                  Complete the DoorDash login in that window. The session and profile are
                  captured the moment it lands.
                </p>
              </div>
            </div>
            <DialogFooter>
              <DialogClose render={<Button variant="outline" />}>
                Hide — capture keeps running
              </DialogClose>
            </DialogFooter>
          </>
        ) : null}

        {phase === "captured" ? (
          <>
            <div className="flex flex-col items-center gap-4 py-6 text-center">
              <div className="flex size-12 items-center justify-center rounded-full bg-status-success/10 ring-1 ring-status-success/25">
                <CircleCheck className="size-6 text-status-success-fg" />
              </div>
              <div className="space-y-1">
                <p className="text-sm font-medium">Session captured</p>
                <p className="text-sm text-muted-foreground">
                  {captured?.name || "Customer"}
                  {captured?.email ? ` · ${captured.email}` : ""}
                </p>
              </div>
            </div>
            <DialogFooter>
              <Button variant="outline" onClick={resetToIdle}>
                Add another
              </Button>
              <DialogClose render={<Button />}>Done</DialogClose>
            </DialogFooter>
          </>
        ) : null}

        {phase === "failed" ? (
          <>
            <Alert variant="destructive">
              <CircleAlert />
              <AlertTitle>Login capture failed</AlertTitle>
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
