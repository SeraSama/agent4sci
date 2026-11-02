// Appearance preferences that are not part of a colour theme: sidebar side, background media, sounds.
// Pure definitions only — the store that loads and saves them lives in ./uistore.
export type Region = 'window' | 'main' | 'sidebar'

export interface BackgroundPrefs {
  asset: string | null
  kind: 'image' | 'video' | null
  opacity: number   // 0–1, how strongly the media shows through
  blur: number      // px
  dim: number       // 0–1, scrim in the background colour, for legibility
  fit: 'cover' | 'contain' | 'tile'
  region: Region
}

export interface SoundPrefs {
  enabled: boolean
  volume: number    // 0–1
}

export interface UiPrefs {
  sidebarSide: 'left' | 'right'
  background: BackgroundPrefs
  sound: SoundPrefs
}

export const DEFAULT_UI: UiPrefs = {
  sidebarSide: 'left',
  background: { asset: null, kind: null, opacity: 0.3, blur: 0, dim: 0.25, fit: 'cover', region: 'window' },
  sound: { enabled: false, volume: 0.5 },
}

export function mergeUi(raw: unknown): UiPrefs {
  const data = (raw ?? {}) as Partial<UiPrefs>
  return {
    ...DEFAULT_UI,
    ...data,
    background: { ...DEFAULT_UI.background, ...(data.background ?? {}) },
    sound: { ...DEFAULT_UI.sound, ...(data.sound ?? {}) },
  }
}

/** True when the active background covers this region of the window. */
export function coversRegion(bg: BackgroundPrefs, region: Exclude<Region, 'window'>): boolean {
  return !!bg.asset && (bg.region === 'window' || bg.region === region)
}
