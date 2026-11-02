import { create } from 'zustand'
import { del, get, put } from '@/lib/api'
import { applyTheme, BUILTIN_THEMES, isValidTheme, type Theme } from './themes'

export interface ThemeSettings {
  mode: 'system' | 'fixed'
  fixed: string
  light: string
  dark: string
  fontSize: number | null  // overrides the theme's own size; null = use the theme's
}

const DEFAULT_SETTINGS: ThemeSettings = { mode: 'system', fixed: 'light', light: 'light', dark: 'dark', fontSize: null }
const CACHE_KEY = 'a4s.theme-cache'

interface ThemeState {
  settings: ThemeSettings
  custom: Theme[]
  preview: Theme | null
  systemDark: boolean
  all: () => Theme[]
  byId: (id: string) => Theme
  active: () => Theme
  load: () => Promise<void>
  setSettings: (patch: Partial<ThemeSettings>) => Promise<void>
  saveCustom: (theme: Theme) => Promise<void>
  deleteCustom: (id: string) => Promise<void>
  setPreview: (theme: Theme | null) => void
}

function readCache(): { settings: ThemeSettings; custom: Theme[] } {
  try {
    const raw = localStorage.getItem(CACHE_KEY)
    if (raw) {
      const data = JSON.parse(raw)
      return { settings: { ...DEFAULT_SETTINGS, ...data.settings }, custom: (data.custom ?? []).filter(isValidTheme) }
    }
  } catch {
    // ignore
  }
  return { settings: DEFAULT_SETTINGS, custom: [] }
}

function writeCache(settings: ThemeSettings, custom: Theme[]): void {
  try {
    localStorage.setItem(CACHE_KEY, JSON.stringify({ settings, custom }))
  } catch {
    // ignore
  }
}

const media = window.matchMedia('(prefers-color-scheme: dark)')
const cached = readCache()

export const useTheme = create<ThemeState>((set, getState) => ({
  settings: cached.settings,
  custom: cached.custom,
  preview: null,
  systemDark: media.matches,
  all: () => [...BUILTIN_THEMES, ...getState().custom],
  byId: (id) => getState().all().find((t) => t.id === id) ?? BUILTIN_THEMES[0],
  active: () => {
    const s = getState()
    if (s.preview) return s.preview
    const { settings } = s
    if (settings.mode === 'fixed') return s.byId(settings.fixed)
    return s.byId(s.systemDark ? settings.dark : settings.light)
  },
  load: async () => {
    try {
      const [prefs, custom] = await Promise.all([
        get<{ theme: ThemeSettings }>('/settings'),
        get<Theme[]>('/themes'),
      ])
      const settings = { ...DEFAULT_SETTINGS, ...(prefs.theme ?? {}) }
      const valid = custom.filter(isValidTheme).map((t) => ({ ...t, builtin: false }))
      set({ settings, custom: valid })
      writeCache(settings, valid)
    } catch {
      // backend not reachable yet: keep cache
    }
  },
  setSettings: async (patch) => {
    const settings = { ...getState().settings, ...patch }
    set({ settings })
    writeCache(settings, getState().custom)
    await put('/settings', { theme: settings })
  },
  saveCustom: async (theme) => {
    const custom = [...getState().custom.filter((t) => t.id !== theme.id), { ...theme, builtin: false }]
    set({ custom })
    writeCache(getState().settings, custom)
    await put(`/themes/${encodeURIComponent(theme.id)}`, theme)
  },
  deleteCustom: async (id) => {
    const custom = getState().custom.filter((t) => t.id !== id)
    const settings = { ...getState().settings }
    for (const key of ['fixed', 'light', 'dark'] as const) {
      if (settings[key] === id) settings[key] = key === 'dark' ? 'dark' : 'light'
    }
    set({ custom, settings })
    writeCache(settings, custom)
    await del(`/themes/${encodeURIComponent(id)}`)
    await put('/settings', { theme: settings })
  },
  setPreview: (theme) => set({ preview: theme }),
}))

media.addEventListener('change', (e) => useTheme.setState({ systemDark: e.matches }))

/** Apply the active theme whenever it changes; keep the native window chrome in sync. */
export function startThemeSync(): () => void {
  let last = ''
  const apply = () => {
    const theme = useTheme.getState().active()
    const s0 = useTheme.getState().settings
    const key = JSON.stringify([theme, s0.fontSize])
    if (key === last) return
    last = key
    applyTheme(s0.fontSize ? { ...theme, fontSize: s0.fontSize } : theme)
    const s = useTheme.getState().settings
    window.a4s?.setNativeTheme(s.mode === 'system' && !useTheme.getState().preview ? 'system' : theme.mode)
  }
  apply()
  return useTheme.subscribe(apply)
}
