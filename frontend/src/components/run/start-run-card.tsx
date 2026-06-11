import { useMemo, useState } from "react"
import { useMutation, useQuery } from "@tanstack/react-query"
import { Link } from "react-router-dom"
import { format, parseISO } from "date-fns"
import { CalendarDays, Loader2, Play, TriangleAlert, UserPlus } from "lucide-react"
import { toast } from "sonner"
import { buttonVariants, Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { api, ApiError } from "@/lib/api"
import type { Customer, StrategyName } from "@/lib/types"
import { useRunStore } from "@/store/runStore"
import { cn } from "@/lib/utils"

const STRATEGY_OPTIONS: Array<{
  value: StrategyName
  label: string
  hint: string
}> = [
  {
    value: "scripted",
    label: "Scripted chat",
    hint: "Follows the configured chat script for every missing refund.",
  },
  {
    value: "llm",
    label: "LLM chat",
    hint: "Lets the configured LLM drive the support conversation.",
  },
  {
    value: "none",
    label: "Detect only",
    hint: "Checks receipts and flags missing refunds without opening chats.",
  },
]

interface Bucket {
  date: string
  count: number
  expired: number
}

function bucketLabel(date: string): string {
  try {
    return format(parseISO(date), "MMM d, yyyy")
  } catch {
    return date
  }
}

function errorDetail(err: unknown): string {
  if (err instanceof ApiError) {
    try {
      const parsed = JSON.parse(err.body) as { detail?: unknown }
      if (typeof parsed.detail === "string") return parsed.detail
    } catch {
      // fall through to generic message
    }
    if (err.status === 409) return "A run is already in progress."
    if (err.status === 400) return "Invalid run request."
  }
  return "Failed to start run."
}

/** Idle-state card: pick a bucket + chat strategy, then POST /api/runs. */
export function StartRunCard() {
  const setActiveRun = useRunStore((s) => s.setActiveRun)
  const [bucketDate, setBucketDate] = useState<string | null>(null)
  const [strategy, setStrategy] = useState<StrategyName>("scripted")

  const customersQuery = useQuery({
    queryKey: ["customers"],
    queryFn: () => api.get<{ customers: Customer[] }>("/customers"),
  })

  const buckets: Bucket[] = useMemo(() => {
    const byDate = new Map<string, Bucket>()
    for (const c of customersQuery.data?.customers ?? []) {
      const b = byDate.get(c.bucket_date) ?? {
        date: c.bucket_date,
        count: 0,
        expired: 0,
      }
      b.count += 1
      if (c.session_status !== "active") b.expired += 1
      byDate.set(c.bucket_date, b)
    }
    return [...byDate.values()].sort((a, b) => b.date.localeCompare(a.date))
  }, [customersQuery.data])

  const selectedBucket = buckets.find((b) => b.date === bucketDate) ?? null
  const selectedStrategy = STRATEGY_OPTIONS.find((s) => s.value === strategy)

  const startRun = useMutation({
    mutationFn: (body: {
      scope: { bucket_date: string }
      chat_strategy: StrategyName
    }) => api.post<{ run_id: number }>("/runs", body),
    onSuccess: ({ run_id }) => {
      setActiveRun(run_id)
      toast.success("Run started", {
        description: `Checking bucket ${bucketDate ? bucketLabel(bucketDate) : ""}.`,
      })
    },
    onError: (err) => toast.error(errorDetail(err)),
  })

  if (customersQuery.isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Start a run</CardTitle>
          <CardDescription>Loading customer buckets…</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <Skeleton className="h-8 w-full" />
          <Skeleton className="h-8 w-full" />
          <Skeleton className="h-8 w-2/3" />
        </CardContent>
      </Card>
    )
  }

  if (buckets.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Start a run</CardTitle>
          <CardDescription>
            You need at least one customer with a captured session before a run
            can start.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Link
            to="/"
            className={cn(buttonVariants({ variant: "outline" }), "w-full")}
          >
            <UserPlus data-icon="inline-start" />
            Add a customer
          </Link>
        </CardContent>
      </Card>
    )
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Start a run</CardTitle>
        <CardDescription>
          Pick a customer bucket and how chats should be handled.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        <div className="space-y-2">
          <Label>Bucket</Label>
          <Select
            items={buckets.map((b) => ({
              value: b.date,
              label: `${bucketLabel(b.date)} · ${b.count}`,
            }))}
            value={bucketDate}
            onValueChange={(value) => setBucketDate(value as string | null)}
          >
            <SelectTrigger className="w-full">
              <SelectValue placeholder="Choose a bucket date" />
            </SelectTrigger>
            <SelectContent>
              {buckets.map((b) => (
                <SelectItem key={b.date} value={b.date}>
                  <span className="flex items-center gap-2">
                    <CalendarDays className="size-3.5 text-muted-foreground" />
                    {bucketLabel(b.date)}
                    <span className="text-xs text-muted-foreground">
                      {b.count} customer{b.count === 1 ? "" : "s"}
                    </span>
                  </span>
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {selectedBucket && selectedBucket.expired > 0 ? (
            <p className="flex items-center gap-1.5 text-xs text-amber-600 dark:text-amber-400">
              <TriangleAlert className="size-3.5" />
              {selectedBucket.expired} of {selectedBucket.count} session
              {selectedBucket.expired === 1 ? " is" : "s are"} not active and may
              be skipped.
            </p>
          ) : null}
        </div>

        <div className="space-y-2">
          <Label>Chat strategy</Label>
          <Select
            items={STRATEGY_OPTIONS.map((s) => ({
              value: s.value,
              label: s.label,
            }))}
            value={strategy}
            onValueChange={(value) => setStrategy((value as StrategyName) ?? "scripted")}
          >
            <SelectTrigger className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {STRATEGY_OPTIONS.map((s) => (
                <SelectItem key={s.value} value={s.value}>
                  {s.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {selectedStrategy ? (
            <p className="text-xs text-muted-foreground">{selectedStrategy.hint}</p>
          ) : null}
        </div>
      </CardContent>
      <CardFooter>
        <Button
          className="w-full"
          disabled={!bucketDate || startRun.isPending}
          onClick={() => {
            if (!bucketDate) return
            startRun.mutate({
              scope: { bucket_date: bucketDate },
              chat_strategy: strategy,
            })
          }}
        >
          {startRun.isPending ? (
            <Loader2 data-icon="inline-start" className="animate-spin" />
          ) : (
            <Play data-icon="inline-start" />
          )}
          {startRun.isPending ? "Starting…" : "Start run"}
        </Button>
      </CardFooter>
    </Card>
  )
}
