import { Copy } from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { CopyValue } from "./copy-cell"

/** One created account, accumulated from per-account `account_created` events. */
export interface CreatedRow {
  customer_id: number
  name: string
  email: string
  email_password: string
  phone: string
  daisy_id: string
  full_address: string
  /** Miles from the chosen anchor — null when unknown (unique=false). */
  dist_from_anchor: number | null
  bucket_date: string
}

async function copyText(text: string, what: string) {
  if (!text) return
  try {
    await navigator.clipboard.writeText(text)
    toast.success(`Copied ${what}`)
  } catch {
    toast.error("Could not copy")
  }
}

/**
 * The post-creation results table — Name · Email · Email PW · Phone · ID ·
 * Address · Dist, every cell one-click-copyable, plus bulk-copy blocks (all
 * emails / all phones / a combined tab-separated dump). This is the webapp's
 * edge over the CustomerDaisy terminal transcript.
 */
export function ResultsTable({ rows }: { rows: CreatedRow[] }) {
  if (rows.length === 0) return null

  const emails = rows.map((r) => r.email).filter(Boolean).join("\n")
  const phones = rows.map((r) => r.phone).filter(Boolean).join("\n")
  // Combined dump mirrors the order in the CustomerDaisy paste:
  // name, email, email-password, phone, address.
  const combined = rows
    .map((r) =>
      [r.name, r.email, r.email_password, r.phone, r.full_address].join("\t"),
    )
    .join("\n")

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2">
        <Button
          variant="outline"
          size="sm"
          disabled={!emails}
          onClick={() => void copyText(emails, "all emails")}
        >
          <Copy data-icon="inline-start" />
          Emails
        </Button>
        <Button
          variant="outline"
          size="sm"
          disabled={!phones}
          onClick={() => void copyText(phones, "all phones")}
        >
          <Copy data-icon="inline-start" />
          Phones
        </Button>
        <Button
          variant="outline"
          size="sm"
          disabled={!combined}
          onClick={() => void copyText(combined, "all rows (tab-separated)")}
        >
          <Copy data-icon="inline-start" />
          All (TSV)
        </Button>
      </div>

      <div className="overflow-x-auto rounded-lg border border-border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Email</TableHead>
              <TableHead>Email PW</TableHead>
              <TableHead>Phone</TableHead>
              <TableHead>ID</TableHead>
              <TableHead>Address</TableHead>
              <TableHead className="text-right">Dist</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((r) => {
              // customer_id is -1 when the DB id is unknown — that's truthy in
              // JS, so guard it explicitly for both the React key and display.
              const idText =
                r.customer_id > 0 ? String(r.customer_id) : ""
              return (
              <TableRow key={idText || r.email}>
                <TableCell>
                  <CopyValue value={r.name} label="name" />
                </TableCell>
                <TableCell>
                  <CopyValue value={r.email} label="email" mono />
                </TableCell>
                <TableCell>
                  <CopyValue
                    value={r.email_password}
                    label="email password"
                    mono
                  />
                </TableCell>
                <TableCell>
                  <CopyValue value={r.phone} label="phone" mono />
                </TableCell>
                <TableCell>
                  <CopyValue value={idText} label="id" mono />
                </TableCell>
                <TableCell className="max-w-[16rem]">
                  <CopyValue value={r.full_address} label="address" />
                </TableCell>
                <TableCell className="num text-right text-muted-foreground">
                  {r.dist_from_anchor != null
                    ? `${r.dist_from_anchor.toFixed(1)} mi`
                    : "—"}
                </TableCell>
              </TableRow>
              )
            })}
          </TableBody>
        </Table>
      </div>
    </div>
  )
}
