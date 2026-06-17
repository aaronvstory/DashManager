import { Palette } from "lucide-react"
import { useTheme } from "next-themes"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { coerceTheme, THEME_LABELS, THEME_ORDER } from "@/lib/themes"

/**
 * Header theme picker — switches between the four named themes (color + radius +
 * font). Mirrors BalTracker's ThemeSwitcher. `coerceTheme` keeps the trigger
 * label valid on first paint (theme is undefined) and against any legacy stored
 * value.
 */
export function ThemeSelector() {
  const { theme, setTheme } = useTheme()
  return (
    <Select
      value={coerceTheme(theme)}
      onValueChange={(v) => {
        if (v) setTheme(v as string)
      }}
    >
      <SelectTrigger size="sm" aria-label="Theme" className="gap-2">
        <Palette className="size-4 text-muted-foreground" />
        <SelectValue />
      </SelectTrigger>
      <SelectContent align="end">
        {THEME_ORDER.map((t) => (
          <SelectItem key={t} value={t}>
            {THEME_LABELS[t]}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}
