import type { LucideIcon } from "lucide-react"
import type { ReactNode } from "react"
import { Card } from "@/components/ui/card"

export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
}: {
  icon: LucideIcon
  title: string
  description: string
  action?: ReactNode
}) {
  return (
    <Card className="flex flex-col items-center justify-center gap-1 border-dashed px-8 py-20 text-center shadow-none">
      <div className="mb-3 flex size-12 items-center justify-center rounded-full bg-muted ring-1 ring-border">
        <Icon className="size-5 text-muted-foreground" />
      </div>
      <h2 className="text-base font-medium">{title}</h2>
      <p className="max-w-sm text-sm text-balance text-muted-foreground">{description}</p>
      {action ? <div className="mt-4">{action}</div> : null}
    </Card>
  )
}
