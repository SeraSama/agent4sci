// Loads and saves the appearance preferences. The backend owns them (so they ride along in backups and
// config bundles); localStorage mirrors them so the first paint after launch already has the right layout.
import { create } from 'zustand'
import { get, put } from './api'
import { DEFAULT_UI, mergeUi, type BackgroundPrefs, type SoundPrefs, type UiPrefs } from './uiprefs'

const CACHE_KEY = 'a4s.ui-cache'

function readCache(): UiPrefs {
  try {
    const raw = localStorage.getItem(CACHE_KEY)
    return raw ? mergeUi(JSON.parse(raw)) : DEFAULT_UI
  } catch {
    return DEFAULT_UI
  }
}

function writeCache(prefs: UiPrefs): void {
  try {
    localStorage.setItem(CACHE_KEY, JSON.stringify(prefs))
  } catch {
    // storage unavailable
  }
}

interface UiState {
  ui: UiPrefs
  load: () => Promise<void>
  set: (patch: Partial<UiPrefs>) => Promise<void>
  setBackground: (patch: Partial<BackgroundPrefs>) => Promise<void>
  setSound: (patch: Partial<SoundPrefs>) => Promise<void>
}

export const useUi = create<UiState>((setState, getState) => {
  const save = async (ui: UiPrefs) => {
    setState({ ui })
    writeCache(ui)
    await put('/settings', { ui })
  }
  return {
    ui: readCache(),
    load: async () => {
      try {
        const prefs = await get<{ ui?: unknown }>('/settings')
        const ui = mergeUi(prefs.ui)
        setState({ ui })
        writeCache(ui)
      } catch {
        // backend not reachable yet: keep the cached copy
      }
    },
    set: (patch) => save({ ...getState().ui, ...patch }),
    setBackground: (patch) => save({ ...getState().ui, background: { ...getState().ui.background, ...patch } }),
    setSound: (patch) => save({ ...getState().ui, sound: { ...getState().ui.sound, ...patch } }),
  }
})
