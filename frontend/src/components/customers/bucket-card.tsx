import { useState } from "react"
import { format, isToday } from "date-fns"
import {
  CalendarDays,
  Ellipsis,
  LoaderCircle,
  LogIn,
  Pencil,
  Play,
  ShieldCheck,
  Smartphone,
  Trash2,
} from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Calendar } from "@/components/ui/calendar"
import {
  Card,
  CardAction,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Checkbox } from "@/components/ui/checkbox"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import type { Customer } from "@/lib/types"
import { cn } from "@/lib/utils"
import { customerName, hasRealName, parseBucketDate, parseDbTimestamp } from "./helpers"
import { CustomerPills } from "./customer-pills"
import { SessionStatusBadge } from "./session-status-badge"

interface RowCallbacks {
  onEdit: (c: Customer) => void
  onDelete: (c: Customer) => void
  onTestSession: (c: Customer) => void
  onMove: (c: Customer, newDate: string) => void
  onFetchOtp: (c: Customer) => void
  onLogin: (c: Customer) => void
}

function RowActions({
  customer,
  testing,
  onEdit,
  onDelete,
  onTestSession,
  onMove,
  onFetchOtp,
  onLogin,
}: RowCallbacks & { customer: Customer; testing: boolean }) {
  const [moveOpen, setMoveOpen] = useState(false)
  // Customers not created via the account flow have no saved number — they
  // can't fetch a fresh OTP or be auto-logged-in.
  const hasNumber = Boolean(customer.number_token)

  return (
    <div className="relative flex items-center justify-end">
      {/* Invisible anchor so the calendar popover opens next to the actions button. */}
      <Popover open={moveOpen} onOpenChange={setMoveOpen}>
        <PopoverTrigger
          render={<span aria-hidden className="absolute inset-y-0 right-0 w-0" />}
        />
        <PopoverContent align="end" className="w-auto p-0">
          <Calendar
            mode="single"
            selected={parseBucketDate(customer.bucket_date)}
            defaultMonth={parseBucketDate(customer.bucket_date)}
            onSelect={(d) => {
              if (d) {
                onMove(customer, format(d, "yyyy-MM-dd"))
                setMoveOpen(false)
              }
            }}
          />
        </PopoverContent>
      </Popover>

      <DropdownMenu>
        <DropdownMenuTrigger
          render={
            <Button
              variant="ghost"
              size="icon-sm"
              disabled={testing}
              aria-label={`Actions for ${customerName(customer)}`}
            />
          }
        >
          {testing ? <LoaderCircle className="animate-spin" /> : <Ellipsis />}
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-48">
          <DropdownMenuItem onClick={() => onEdit(customer)}>
            <Pencil />
            Edit details
          </DropdownMenuItem>
          <DropdownMenuItem
            onClick={() => {
              // Open after the menu's close so its dismiss handlers don't eat the popover.
              setTimeout(() => setMoveOpen(true), 0)
            }}
          >
            <CalendarDays />
            Move to date
          </DropdownMenuItem>
          <DropdownMenuItem onClick={() => onTestSession(customer)}>
            <ShieldCheck />
            Test session
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem
            disabled={!hasNumber}
            onClick={() => onFetchOtp(customer)}
            title={
              hasNumber
                ? undefined
                : "No saved number (not created via the account flow)"
            }
          >
            <Smartphone />
            Fetch OTP
          </DropdownMenuItem>
          <DropdownMenuItem
            disabled={!hasNumber}
            onClick={() => onLogin(customer)}
            title={
              hasNumber
                ? undefined
                : "No saved number (not created via the account flow)"
            }
          >
            <LogIn />
            Log in
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem variant="destructive" onClick={() => onDelete(customer)}>
            <Trash2 />
            Delete
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  )
}

export function BucketCard({
  date,
  customers,
  selectedIds,
  testingIds,
  runDisabled,
  onToggleRow,
  onToggleBucket,
  onRunBucket,
  ...rowCallbacks
}: RowCallbacks & {
  date: string
  customers: Customer[]
  selectedIds: ReadonlySet<number>
  testingIds: ReadonlySet<number>
  runDisabled: boolean
  onToggleRow: (id: number, checked: boolean) => void
  onToggleBucket: (ids: number[], checked: boolean) => void
  onRunBucket: (date: string) => void
}) {
  const bucketDate = parseBucketDate(date)
  const ids = customers.map((c) => c.id)
  const selectedCount = ids.filter((id) => selectedIds.has(id)).length
  const allSelected = selectedCount === ids.length && ids.length > 0

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          {format(bucketDate, "EEE, MMM d yyyy")}
          {isToday(bucketDate) ? (
            <Badge className="border-primary/20 bg-primary/10 text-primary">Today</Badge>
          ) : null}
        </CardTitle>
        <CardDescription>
          {customers.length} customer{customers.length === 1 ? "" : "s"}
          {selectedCount > 0 ? ` · ${selectedCount} selected` : ""}
        </CardDescription>
        <CardAction>
          <Button
            variant="outline"
            size="sm"
            disabled={runDisabled}
            onClick={() => onRunBucket(date)}
            title="Start a refund-check run on this bucket: scrape every order, detect missing refunds, and pursue them"
          >
            <Play data-icon="inline-start" className="text-primary" />
            Check refunds
          </Button>
        </CardAction>
      </CardHeader>

      <Table>
        <TableHeader>
          <TableRow className="hover:bg-transparent">
            <TableHead className="w-10 pl-4">
              <Checkbox
                checked={allSelected}
                indeterminate={selectedCount > 0 && !allSelected}
                onCheckedChange={(checked) => onToggleBucket(ids, checked)}
                aria-label="Select all customers in this bucket"
              />
            </TableHead>
            <TableHead>Name</TableHead>
            <TableHead>Email</TableHead>
            <TableHead>Phone</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Added</TableHead>
            <TableHead className="w-12 pr-4 text-right">
              <span className="sr-only">Actions</span>
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {customers.map((c) => {
            const selected = selectedIds.has(c.id)
            const testing = testingIds.has(c.id)
            return (
              <TableRow key={c.id} data-state={selected ? "selected" : undefined}>
                <TableCell className="pl-4">
                  <Checkbox
                    checked={selected}
                    onCheckedChange={(checked) => onToggleRow(c.id, checked)}
                    aria-label={`Select ${customerName(c)}`}
                  />
                </TableCell>
                <TableCell
                  className={cn(
                    "font-medium",
                    !hasRealName(c) && "font-normal text-muted-foreground italic",
                  )}
                >
                  {customerName(c)}
                </TableCell>
                <TableCell className="text-muted-foreground">{c.email || "—"}</TableCell>
                <TableCell className="text-muted-foreground">{c.phone || "—"}</TableCell>
                <TableCell>
                  {testing ? (
                    <Badge variant="outline" className="gap-1.5 text-muted-foreground">
                      <LoaderCircle className="animate-spin" />
                      Testing…
                    </Badge>
                  ) : c.pills ? (
                    <CustomerPills pills={c.pills} />
                  ) : (
                    <SessionStatusBadge status={c.session_status} />
                  )}
                </TableCell>
                <TableCell className="text-muted-foreground">
                  {format(parseDbTimestamp(c.created_at), "MMM d, yyyy")}
                </TableCell>
                <TableCell className="pr-4">
                  <RowActions customer={c} testing={testing} {...rowCallbacks} />
                </TableCell>
              </TableRow>
            )
          })}
        </TableBody>
      </Table>
    </Card>
  )
}
