import { NavLink, Outlet, useLocation } from "react-router-dom"
import { Database, FileText, Flower2, History, Network, Play, Settings, Smartphone, Users } from "lucide-react"
import type { LucideIcon } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Logo } from "@/components/logo"
import { ThemeToggle } from "@/components/theme-toggle"
import { useEvents } from "@/lib/sse"
import { useRunStore } from "@/store/runStore"
import { cn } from "@/lib/utils"

interface NavItem {
  to: string
  label: string
  icon: LucideIcon
  end?: boolean
}

// Each item has ONE clear job. "Customers" = the bucket board (manage who's in
// each date bucket). "Customer Data" = the live per-customer database (sessions,
// every scraped order, raw audit). "Reports" = the frozen daily refund worklog
// (proof + transcripts). "Refund Run" = launch/monitor a refund-check run. "OTP"
// = live SMS codes (by bucket or by batch).
const NAV: NavItem[] = [
  { to: "/", label: "Customers", icon: Users, end: true },
  { to: "/daisy", label: "CustomerDaisy", icon: Flower2 },
  { to: "/database", label: "Customer Data", icon: Database },
  { to: "/run", label: "Refund Run", icon: Play },
  { to: "/otp", label: "OTP", icon: Smartphone },
  { to: "/history", label: "History", icon: History },
  { to: "/reports", label: "Reports", icon: FileText },
  { to: "/proxies", label: "Proxies", icon: Network },
  { to: "/settings", label: "Settings", icon: Settings },
]

function ConnectionStatus() {
  const connected = useRunStore((s) => s.connected)
  return (
    <div className="flex items-center gap-2 px-3 text-xs text-sidebar-foreground/60">
      <span className="relative flex size-2">
        {connected ? (
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-500/60" />
        ) : null}
        <span
          className={cn(
            "relative inline-flex size-2 rounded-full",
            connected ? "bg-emerald-500" : "bg-zinc-500",
          )}
        />
      </span>
      {connected ? "Live — events connected" : "Offline — waiting for backend"}
    </div>
  )
}

export function AppLayout() {
  // Open the SSE stream once for the whole app.
  useEvents()
  const runActive = useRunStore((s) => s.runActive)
  const { pathname } = useLocation()
  const current = NAV.find((n) => (n.end ? pathname === n.to : pathname.startsWith(n.to)))

  return (
    <div className="flex h-dvh bg-background">
      {/* Sidebar */}
      <aside className="flex w-60 shrink-0 flex-col border-r border-sidebar-border bg-sidebar">
        <div className="flex h-14 items-center border-b border-sidebar-border px-4">
          <Logo />
        </div>

        <nav className="flex-1 space-y-1 overflow-y-auto p-3">
          {NAV.map(({ to, label, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) =>
                cn(
                  "group flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors",
                  isActive
                    ? "bg-primary/10 text-primary"
                    : "text-sidebar-foreground/70 hover:bg-sidebar-accent hover:text-sidebar-foreground",
                )
              }
            >
              {({ isActive }) => (
                <>
                  <Icon
                    className={cn(
                      "size-4 transition-colors",
                      isActive
                        ? "text-primary"
                        : "text-sidebar-foreground/50 group-hover:text-sidebar-foreground",
                    )}
                  />
                  {label}
                  {isActive ? (
                    <span className="ml-auto size-1.5 rounded-full bg-primary" />
                  ) : null}
                </>
              )}
            </NavLink>
          ))}
        </nav>

        <div className="border-t border-sidebar-border py-4">
          <ConnectionStatus />
        </div>
      </aside>

      {/* Main column */}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 shrink-0 items-center justify-between border-b border-border bg-background/80 px-6 backdrop-blur">
          <span className="text-sm font-medium text-muted-foreground">
            {current?.label ?? "DashManager"}
          </span>
          <div className="flex items-center gap-3">
            {runActive ? (
              <Badge className="border-primary/20 bg-primary/10 text-primary">
                Run active
              </Badge>
            ) : null}
            <ThemeToggle />
          </div>
        </header>

        <main className="flex-1 overflow-y-auto">
          <div className="mx-auto w-full max-w-[1600px] p-8">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  )
}
