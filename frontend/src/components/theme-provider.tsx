import { ThemeProvider as NextThemesProvider } from "next-themes"
import type { ReactNode } from "react"
import { DEFAULT_THEME, THEME_ORDER } from "@/lib/themes"

/**
 * Named-theme switching via next-themes: writes `data-theme="<name>"` on <html>,
 * persisted to localStorage. The four themes are all dark.
 *
 * LOAD-BEARING: index.html ships a permanent `class="dark"` that next-themes
 * never touches (it manages `data-theme` only). That class keeps every `dark:`
 * utility live now that there's no light mode — do not remove it.
 *
 * Read theme state with `useTheme` from "next-themes" (theme-selector.tsx and
 * components/ui/sonner.tsx).
 */
export function ThemeProvider({ children }: { children: ReactNode }) {
  return (
    <NextThemesProvider
      attribute="data-theme"
      themes={[...THEME_ORDER]}
      defaultTheme={DEFAULT_THEME}
      enableSystem={false}
      // Themes aren't named "light"/"dark", so next-themes can't drive
      // color-scheme correctly — we pin it to dark in CSS instead (index.css
      // html rule). Let it manage data-theme only.
      enableColorScheme={false}
      storageKey="dashmanager-theme"
      disableTransitionOnChange
    >
      {children}
    </NextThemesProvider>
  )
}
