/**
 * The named themes, ported verbatim from BalTracker. Each is a complete dark
 * look (color + corner radius + display font), defined as a `[data-theme="x"]`
 * block in index.css and selected via next-themes (attribute="data-theme").
 */
export const THEME_ORDER = [
  "brutalist",
  "editorial",
  "notion",
  "bento",
] as const

export type ThemeName = (typeof THEME_ORDER)[number]

export const THEME_LABELS: Record<ThemeName, string> = {
  brutalist: "Brutalist",
  editorial: "Editorial Amber",
  notion: "Notion Dark",
  bento: "Bento",
}

export const DEFAULT_THEME: ThemeName = "brutalist"

/**
 * Map any stored/active value to a valid theme. Guards the legacy "dark"/"light"
 * values older builds persisted to localStorage, and next-themes' `undefined`
 * on first paint — both fall back to the default so the selector never shows a
 * blank or invalid label.
 */
export function coerceTheme(value: string | undefined | null): ThemeName {
  return (THEME_ORDER as readonly string[]).includes(value ?? "")
    ? (value as ThemeName)
    : DEFAULT_THEME
}
