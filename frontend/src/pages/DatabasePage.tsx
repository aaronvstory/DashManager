import { useMemo } from "react"
import { useQuery } from "@tanstack/react-query"
import { Check, Database, ServerCrash, Sparkles, Users } from "lucide-react"
import { EmptyState } from "@/components/empty-state"
import { PageHeader } from "@/components/page-header"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { DatabaseBucket } from "@/components/database/database-bucket"
import { api } from "@/lib/api"
import type { FullCustomer } from "@/lib/types"

function LoadingSkeleton() {
  return (
    <div className="space-y-6">
      <div className="grid gap-4 sm:grid-cols-3">
        {[0, 1, 2].map((i) => (
          <Card key={i}>
            <CardContent className="space-y-2">
              <Skeleton className="h-3 w-20" />
              <Skeleton className="h-7 w-12" />
            </CardContent>
          </Card>
        ))}
      </div>
      {[0, 1].map((i) => (
        <Card key={i}>
          <CardHeader className="border-b">
            <Skeleton className="h-5 w-48" />
          </CardHeader>
          <CardContent className="space-y-2.5">
            {[0, 1, 2].map((j) => (
              <Skeleton key={j} className="h-12 w-full" />
            ))}
          </CardContent>
        </Card>
      ))}
    </div>
  )
}

function StatCard({
  icon: Icon,
  label,
  value,
}: {
  icon: typeof Users
  label: string
  value: number
}) {
  return (
    <Card className="relative">
      {/* solid red top rail — flat, no gradient */}
      <span aria-hidden className="absolute inset-x-0 top-0 h-0.5 bg-primary" />
      <CardContent className="flex items-center gap-3.5">
        <div className="flex size-10 shrink-0 items-center justify-center border border-primary/30 bg-primary/10">
          <Icon className="size-4.5 text-primary" />
        </div>
        <div className="space-y-0.5">
          <p className="eyebrow">{label}</p>
          <p className="num text-2xl font-bold">{value}</p>
        </div>
      </CardContent>
    </Card>
  )
}

export default function DatabasePage() {
  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ["customers-full"],
    queryFn: () => api.get<{ customers: FullCustomer[] }>("/customers/full"),
  })

  const customers = useMemo(() => data?.customers ?? [], [data])

  /** bucket_date → customers, newest bucket first. */
  const buckets = useMemo(() => {
    const map = new Map<string, FullCustomer[]>()
    for (const c of customers) {
      const list = map.get(c.bucket_date)
      if (list) list.push(c)
      else map.set(c.bucket_date, [c])
    }
    return [...map.entries()].sort((a, b) => (a[0] < b[0] ? 1 : -1))
  }, [customers])

  const loggedIn = customers.filter(
    (c) => c.pills.lifecycle === "logged_in",
  ).length
  const created = customers.length - loggedIn

  return (
    <>
      <PageHeader
        title="Database"
        description="Everything captured for each customer — sessions, identities, and scraped orders. Read-only."
      />

      {isLoading ? (
        <LoadingSkeleton />
      ) : isError ? (
        <EmptyState
          icon={ServerCrash}
          title="Couldn't load the database"
          description={
            error instanceof Error ? error.message : "The backend did not respond."
          }
          action={<Button onClick={() => void refetch()}>Retry</Button>}
        />
      ) : customers.length === 0 ? (
        <EmptyState
          icon={Database}
          title="Nothing here yet"
          description="Once customers are added and a run scrapes their orders, the full picture shows up here."
        />
      ) : (
        <div className="space-y-6">
          <div className="grid gap-4 sm:grid-cols-3">
            <StatCard icon={Users} label="Customers" value={customers.length} />
            <StatCard icon={Database} label="Buckets" value={buckets.length} />
            <StatCard icon={Check} label="Logged in" value={loggedIn} />
          </div>

          {created > 0 ? (
            <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <Sparkles className="size-3.5 text-primary" />
              {loggedIn} logged in · {created} created (not yet logged in)
            </p>
          ) : null}

          <div className="space-y-6">
            {buckets.map(([date, bucketCustomers]) => (
              <DatabaseBucket
                key={date}
                date={date}
                customers={bucketCustomers}
              />
            ))}
          </div>
        </div>
      )}
    </>
  )
}
