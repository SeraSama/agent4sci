export interface A4SBridge {
  backend: { url: string; token: string } | null
  platform: string
  pickFolder: () => Promise<string | null>
  pickFile: (kind: 'media') => Promise<string | null>
  pathForFile: (file: File) => string | null
  openPath: (path: string) => Promise<string>
  openExternal: (url: string) => Promise<void>
  setNativeTheme: (mode: 'light' | 'dark' | 'system') => void
  restartBackend: () => Promise<{ url: string; token: string }>
  onBackendExited: (listener: (code: number | null) => void) => void
}

declare global {
  interface Window {
    a4s?: A4SBridge
  }
}
