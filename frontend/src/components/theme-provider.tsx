import { ThemeProvider as NextThemesProvider } from "next-themes"
import type { ReactNode } from "react"

/**
 * Class-based theme switching (`.dark` on <html>), persisted to localStorage.
 * Dark is the default; index.html ships class="dark" to avoid a light flash.
 *
 * Read theme state with `useTheme` from "next-themes"
 * (components/ui/sonner.tsx and theme-toggle.tsx already do).
 */
export function ThemeProvider({ children }: { children: ReactNode }) {
  return (
    <NextThemesProvider
      attribute="class"
      defaultTheme="dark"
      enableSystem={false}
      storageKey="dashmanager-theme"
      disableTransitionOnChange
    >
      {children}
    </NextThemesProvider>
  )
}
