/**
 * Create — the front door of the pipeline: mint (or adopt) DoorDash accounts,
 * then flow straight into OTP capture → keep-open/login → refund audit.
 *
 * The full creation flow lives in CreateAccountDialog (anchor picker, radius,
 * count, headed/headless, live per-account progress, results table). This page
 * is its dedicated home: it opens that dialog on arrival and, once closed,
 * leaves a short intro with a button to start again — so "Create" is a real
 * nav destination, not a button buried on the Customers board.
 */

import { useState } from "react"
import { Link } from "react-router-dom"
import { ArrowRight, Smartphone, Sparkles, UserPlus } from "lucide-react"
import { CreateAccountDialog } from "@/components/customers/create-account-dialog"
import { PageHeader } from "@/components/page-header"
import { Button } from "@/components/ui/button"

export default function CreatePage() {
  // Open the dialog on arrival — landing on "Create" means you came here to
  // create. The router remounts this page on each navigation to /create, so
  // this initial value re-opens it every visit. Closing falls back to the
  // intro card (the header button reopens it).
  const [open, setOpen] = useState(true)

  return (
    <>
      <PageHeader
        title="Create"
        description="Sign up brand-new DoorDash accounts (generated identity, email, phone, and an address near a chosen location) — or adopt ones already in CustomerDaisy. The next steps are OTP capture, then logging them in for the refund audit."
        actions={
          <Button onClick={() => setOpen(true)}>
            <UserPlus data-icon="inline-start" />
            New account(s)
          </Button>
        }
      />

      <div className="max-w-prose space-y-4">
        <div className="border border-border bg-card p-5">
          <div className="flex items-center gap-2 text-sm font-medium">
            <Sparkles className="size-4 text-primary" />
            How creation works
          </div>
          <ol className="mt-3 space-y-2 text-sm text-muted-foreground">
            <li>
              <strong className="text-foreground">1. Pick a location &amp; count.</strong>{" "}
              Each account gets a generated identity and an address within the
              radius of your chosen anchor.
            </li>
            <li>
              <strong className="text-foreground">2. Watch it run.</strong> A real
              Chrome window signs up each account and enters the SMS code
              automatically. Headed runs drive your actual mouse and keyboard —
              don&apos;t touch the PC until they finish.
            </li>
            <li>
              <strong className="text-foreground">3. Continue the workflow.</strong>{" "}
              When the batch finishes, grab the codes on the OTP page, keep the
              browsers open to log in, then run the refund audit.
            </li>
          </ol>
        </div>

        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <span>Next:</span>
          <Button
            variant="outline"
            size="sm"
            render={
              <Link to="/otp">
                <Smartphone data-icon="inline-start" />
                OTP page
                <ArrowRight data-icon="inline-end" />
              </Link>
            }
          />
        </div>
      </div>

      <CreateAccountDialog open={open} onOpenChange={setOpen} />
    </>
  )
}
